#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2012 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <subi 2012/03/26 13:27:18>
#
# mplayer-wrapper is an MPlayer frontend, trying to be a transparent interface.
# It is convenient to rename the script to "mplayer" and place it in your $PATH
# (don't overwrite the real MPlayer); you would not even notice its existence.

# TODO:
# 1. resume last played position
# 2. remember last volume
# 3. remember last volume/hue/contrast for continuous playing (don't need data
#    persistance)
# 4. shooter sometimes return a false subtitle with the same time length. find a
#    cure. (using zenity, pygtk, or both?)
# 5. chardet instead of enca?
# 6. support a,b,c... in continuous playing?
# 7. dedicated dir for subs?
# 8. split MPlayer to 2 classes: one holds metainfo, the other for
#    manuplating mplayer instance.
# 9. filehash should be in Media
# 10. xset s off
# 11. retry on failure of fetching

import logging
import os, sys, time
import struct, urllib2
import multiprocessing, subprocess
from fractions import Fraction

def which(cmd):
    """Mimic shell command "which".
    """
    def exefy(fullpath):
        return fullpath if os.access(fullpath, os.X_OK) else None

    pdir = os.path.split(cmd)[0]
    if pdir:
        return exefy(cmd)
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            fullpath = exefy(os.path.join(path,cmd))
            if fullpath: return fullpath
    return None
        
def check_dimension():
    """Select the maximal available screen dimension.
    """
    dim = [640, 480]
    if which("xrandr"):
        p = subprocess.Popen(["xrandr"], stdout = subprocess.PIPE)
        for line in p.communicate()[0].splitlines():
            if line.startswith("*"):
                dim[0] = int(line.split()[1])
                dim[1] = int(line.split()[3])
                break
            elif '*' in line:
                d = line.split()[0].split('x')
                if d[0] > dim[0]: dim = map(int,d)

    return dim + [Fraction(dim[0],dim[1])]

def expand_video(media, method = "ass", display_aspect = Fraction(4,3)):
    """Given a video metainfo "m", expand the video to the specified "display_aspect"
    with "method".

    Return the arguments list for mplayer.

    Video expanding attaches two black bands to the top and bottom of the video.
    MPlayer will then render osds (subtitles etc.) within the bands.
        
    Two ways exist:
    1. -vf expand:
       everything done by mplayer, not compatible with libass (subtitle overlapping
       problem). Have to use the old plain subtitle renderer (-noass).
    2. -ass-use-margin:
       everything done by YOU, including the calculation of the margin heights and
       the font scales. The benefit is you can use "-ass".
        
    The "ass-use-margin" method has a very annoying problem: the subtitle charecters
    are horizontally stretched. We need a fix.
        
    A wild guess for what is done in the ass renderer of mplayer/libass is taken
    after some googling and experiments:
    1. there are 3 different dimensions: the video, the display, and the ass rendering
       screen (PlayResX:PlayResY)
    2. the font scale is caculated w.r.t. the ASS styles of "PlayResX, PlayResY, ScaleX,
       ScaleY" and the mplayer option "-ass-font-scale":
       a. PlayResY defaults to 288
       b. PlayResX defaults to PlayResY/1.3333
       c. ScaleX, ScaleY both defaults to 1
       d. the final font size rendered in the VIDEO is:
               scale_base = video_Y/PlayResY * ass_font_scale 
               font_Y = scale_base * ScaleY
               font_X = scale_base * ScaleX
    3. the font size rendered in the DISPLAY is:
               scale_base_disp = disp_Y/video_Y * scale_base
                               = disp_Y/PlayResY * ass_font_scale
               font_Y_disp = scale_base_disp * ScaleY
               font_X_disp = scale_base_disp * ScaleX
        
    This is why the subtitle is alway of the same size with the same display size for
    different videos. (disp_Y/PlayResY is constant when PlayResY takes the default
    value.)
        
    While the video expanding (in Y axis) is concerned, the situation becomes a little
    messy:
             ex_scale_base = ex_video_Y/PlayResY * ass_font_scale
             ex_font_Y = (video_Y/ex_video_Y) * ex_scale_base * ScaleY
                       = font_Y
             ex_font_X = ex_scale_base * ScaleX
                       = ex_video_Y/PlayResY * ass_font_scale * ScaleX
                       = (ex_video_Y/video_Y) * font_X
    Clearly the subtitle is horizontally stretched (vertically unchanged).
        
    So, what we need is:
    1. do expanding (easy)
    2. make font be of correct aspect (simply let ScaleX = video_Y/ex_video_Y )
        
    Addtionally, we also want to place the subtitle as close to the picture as
    possible (instead of the bottom of the screen, which is visual distracting).
    This can be done via the ASS style tag "MarginV", which is the relative relative
    in the ass rendering screen (i.e. the screan of PlayResX:PlayResY).
        
    Another approach is just adding a black band that is wide enough to contain
    the subtitles, avoiding the use of "MarginV".
    """
    # -subfont-autoscale has nothing to do with libass, only affect the osd and
    # the plain old subtitle renderer
    args = "-subfont-autoscale 2".split()

    if media.scaled_dimension[2] < Fraction(4,3):
        # assume we will never face a narrower screen than 4:3
        args.extend("-vf-pre dsize=4/3".split())
    elif method == "ass":
        # feel free to change the scale for your favor.
        ass_font_scale = 2
        args.extend("-ass -ass-font-scale {0}".format(ass_font_scale).split());

        subtitle_height_in_video = int(18*1.25/288 * ass_font_scale * media.scaled_dimension[1])
        target_aspect = Fraction(media.scaled_dimension[0], media.scaled_dimension[1]+subtitle_height_in_video*2)
        if target_aspect < display_aspect:
            target_aspect = display_aspect

        # expand_video_y:video_Y = (video_X/video_Y):(video_X/expanded_video_Y)
        m2t = media.scaled_dimension[2] / target_aspect
        
        if m2t > 1:
            margin = (m2t - 1) * media.scaled_dimension[1] / 2
            args.extend("-ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin)).split())
            args.extend("-ass-force-style ScaleX={0}".format(1/float(m2t)).split())
    else:
        # -vf expand does its own non-square pixel adjustment;
        # m.original_dimension is fine
        args.extend("-subpos 98 -vf-pre expand={0}::::1:{1}".format(media.original_dimension[0],display_aspect).split())
    return args

def genlist(path):
    """For the given path, generate a file list for continuous playing.
    """
    import locale,re
    def translate(s):
        dic = dict(zip(u'零壹贰叁肆伍陆柒捌玖〇一二三四五六七八九','01234567890123456789'))
        loc = locale.getdefaultlocale()
        s = s.decode(loc[1])
        return ''.join([dic.get(c,c) for c in s]).encode(loc[1])
    def split_by_int(s):
        return [x for x in re.split('(\d+)', translate(s)) if x != '']
    def make_sort_key(s):
        return [(int(x) if x.isdigit() else x) for x in split_by_int(s)]
    def strip_to_int(s,prefix):
        if prefix and prefix in s:
            s = s.partition(prefix)[2]
        s = split_by_int(s)[0]
        return int(s) if s.isdigit() else float('NaN')

    if not os.path.exists(path):
        return [path]
    
    pdir, basename = os.path.split(os.path.abspath(path))

    # basic candidate filtering
    # 1. extention
    files = [f for f in os.listdir(pdir) if f.endswith(os.path.splitext(basename)[1])]
    # 2. sort and remove alphabetical header
    files.sort(key=make_sort_key)
    del files[0:files.index(basename)]

    # only one file in the candidate list
    if len(files) == 1:
        return [os.path.join(pdir,basename)]

    # find the common prefix
    keys = [split_by_int(f) for f in files[0:2]]
    prefix_items = []
    for key in zip(keys[0],keys[1]):
        if key[0] == key[1]: prefix_items.append(key[0])
        else: break
    prefix = ''.join(prefix_items)

    # generate the list
    results = [os.path.join(pdir,files[0])]
    for i,f in enumerate(files[1:]):
        if strip_to_int(f,prefix) - strip_to_int(files[i],prefix) == 1:
            results.append(os.path.join(pdir,f))
        else: break
    return results

def handle_shooter_subtitles(media, cmd_conn_write_end):
    def build_req(m):
        def hashing(path):
            sz = os.path.getsize(path)
            if sz>8192:
                import hashlib
                f = open(path, 'rb')
                filehash = ';'.join([(f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1] for s in (lambda l:[4096, l/3*2, l/3, l-8192])(sz)])
                f.close()
            else:
                filehash = ""
            return filehash

        import httplib
        schemas = ["http", "https"] if hasattr(httplib, 'HTTPS') else ["http"]
        servers = ["www", "splayer"] + ["splayer"+str(i) for i in range(1,13)]

        import random
        boundary = "-"*28 + "{0:x}".format(random.getrandbits(48))

        url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(schemas), random.choice(servers))

        header = []
        header.append(["User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543))])
        header.append(["Content-Type", "multipart/form-data; boundary={0}".format(boundary)])

        items = []
        items.append(["pathinfo", os.path.join("c:/",
                                               os.path.basename(os.path.dirname(m.fullpath)),
                                               os.path.basename(m.fullpath))])
        items.append(["filehash", hashing(m.fullpath)])
#        data.append(["lang", "chn"])

        data = ''.join(["--{0}\n"
                        "Content-Disposition: form-data; name=\"{1}\"\n\n"
                        "{2}\n".format(boundary, d[0], d[1]) for d in items]
                       + ["--" + boundary + "--"])

        logging.debug("Querying server {0} with\n"
                      "{1}\n"
                      "{2}\n".format(url,header,data))

        req = urllib2.Request(url)
        for h in header:
            req.add_header(h[0],h[1])
        req.add_data(data)

        return req

    def fetch_sub(req):
        def parse_package(response):
            c = response.read(8)
            package_length, desc_length = struct.unpack("!II", c)
            description = response.read(desc_length).decode("UTF-8")
            if not description: description = "no description"

            logging.info("Length of current package in bytes: {0}".format(package_length))

            c = response.read(5)
            package_length, file_count = struct.unpack("!IB", c)

            logging.info("{0} subtitles in current package ({1})".format(file_count,description))

            subs_in_pack = []
            for j in range(file_count):
                sub = parse_file(response)
                if sub: subs_in_pack.append(sub)
            return subs_in_pack

        def parse_file(response):
            c = response.read(8)
            pack_len, ext_len = struct.unpack("!II", c)
            ext = response.read(ext_len)

            c = response.read(4)
            file_len = struct.unpack("!I", c)[0]
            sub = response.read(file_len)

            if sub.startswith("\x1f\x8b"):
                import gzip
                from cStringIO import StringIO
                return [ext, gzip.GzipFile(fileobj=StringIO(sub)).read()]
            else:
                logging.warning("Unknown format or incomplete data. Trying again...")
                return None

        ### function body

        # wait for mplayer to settle up
        time.sleep(0.5)

        response = urllib2.urlopen(req)

        c = response.read(1)
        package_count = struct.unpack("!b", c)[0]

        logging.info("{0} subtitle packages found".format(package_count))

        subs = []
        for i in range(package_count):
            subs.extend(parse_package(response))
        logging.info("{0} subtitle(s) fetched.".format(len(subs)))

        # todo: enumerate?
        for i in range(len(subs)):
            suffix = str(i) if i>0 else ""
            subs[i][0] = suffix + '.' + subs[i][0]

        return subs

    ### function body
    logging.info("Connecting to shooter server...")
    subs = fetch_sub(build_req(media))

    prefix = os.path.splitext(media.fullpath)[0]

    # save subtitles and generate mplayer fifo commands
    cmds = []
    enca = which("enca")
    for sub in subs:
        path = prefix + sub[0]
        logging.info("Saving subtitle as {0}".format(path))
        f = open(path,"wb")
        f.write(sub[1])
        f.close()
        if enca:
            logging.info("Convert {0} to UTF-8".format(path))
            subprocess.Popen("enca -c -x utf8 -L zh".split()+[path]).communicate()
        cmds.append("sub_load \"{0}\"".format(path))
    if subs:
        cmds.append("sub_file 0")

    cmd_conn_write_end.send(cmds)

#todo: class again
class SubFetcher(object):
    pass

class MPlayer(object):
    # TODO: "not compiled in option"
    last_timestamp = 0.0
    last_exit_status = None

    def identify(self, filelist=[]):
        p = subprocess.Popen([self.__path]+"-vo null -ao null -frames 0 -identify".split()+filelist, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return [l for l in p.communicate()[0].splitlines() if l.startswith("ID_")]

    def support(self, opt):
        # 1: take no param
        # 2: take 1 param
        if not self.__opts: self.__gen_opts()
        return self.__opts[opt] if opt in self.__opts else 0

    def cmd(self, cmd_pipe_read_end):
        def send_one_cmd(c):
            if self.__process.poll() == None:
                logging.debug("Sending command <{0}> to <{1}>".format(c, self.__fifo_path))
                fifo = open(self.__fifo_path,"w")
                fifo.write(c+'\n')
                fifo.close()

        while True:
            if cmd_pipe_read_end.poll():
                for c in cmd_pipe_read_end.recv():
                    send_one_cmd(c)
            else:
                time.sleep(1)
        
    def play(self, args=[], cmd_conn_read_end = None):
        def tee(p, f=sys.stdout):
            def flush(f,lines):
                f.write(''.join(lines.pop(0)))
                f.flush()
                lines.append([])

            # cache 5 lines in case of unexpected outputs
            lines = [[] for i in range(5)]
            while True:
                c = p.stdout.read(1)
                lines[4].append(c)
                if c == '\n':
                    flush(f,lines)
                elif c == '\r':
                    d = p.stdout.read(1)
                    if d == '\n':
                        lines[4].append('\n')
                        flush(f,lines)
                    else:
                        flush(f,lines)
                        lines[4].append(d)
                elif c == '':
                    break
            for l in (''.join(ll) for ll in lines):
                if l.startswith(('A:','V:')):
                    self.last_timestamp = float(l[2:9])
                if l.startswith('Exiting...'):
                    self.last_exit_status = l[12:len(l)-2]
                f.write(l)
                f.flush()

        args = [self.__path] + self.__args + args

        logging.debug("Final command:\n{0}".format(' '.join(args)))

        if dry_run:
            return

        self.__process = subprocess.Popen(args, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=None)

        if cmd_conn_read_end:
            proc_cmd_sender = multiprocessing.Process(target=self.cmd, args=(cmd_conn_read_end,))
            proc_cmd_sender.start()
        
        # block untill self.__process exits
        tee(self.__process)
        if cmd_conn_read_end and proc_cmd_sender.is_alive():
            proc_cmd_sender.terminate()

        logging.debug("Last timestamp: {0}".format(self.last_timestamp))
        logging.debug("Last exit status: {0}".format(self.last_exit_status))

    def __init__(self, fifo, default_args=[]):
        self.__path = None
        self.__fifo_path = fifo.path
        self.__args = fifo.args + default_args
        self.__opts = {}
        self.__process = None
        for p in ["/opt/bin/mplayer","/usr/local/bin/mplayer","/usr/bin/mplayer"]:
            if os.path.isfile(p): self.__path = p
        if not self.__path:
            raise RuntimeError,"Cannot find a mplayer binary."

    def __gen_opts(self):
        options = subprocess.Popen([self.__path, "-list-options"], stdout=subprocess.PIPE).communicate()[0].splitlines()
        options = options[3:len(options)-3]

        for line in options:
            s = line.split();
            opt = s[0].split(":") # take care of option:suboption
            if opt[0] in self.__opts: continue
            self.__opts[opt[0]] = (2 if len(opt)==2 or s[1]!="Flag" else 1)

        # handle vf* af*: mplayer reports option name as vf*, while it
        # is a family of options.
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            self.__opts[extra] = 2
        for extra in ["af-clr","vf-clr"]:
            self.__opts[extra] = 1

class Media:
    """Construct media metadata by midentify; may apply "proper" fixes
    """
    exist = True

    filename = ""
    fullpath = ""
    
    seekable = True
    is_video = False

    original_dimension = [0,0,Fraction(0)]
    scaled_dimension = [0,0,Fraction(0)]

    subtitle_had = "none"

    def __init__(self, path, mplayer):
        """Parse the output by midentify.
        """
        info = {}
        for l in mplayer.identify([path]):
            a = l.split('=')
            info[a[0]] = a[1]

        if not "ID_FILENAME" in info:
            self.exist = False;
            return

        self.__gen_meta_info(info)
        
        if self.is_video:
            self.__gen_video_info(info)
            self.__gen_subtitle_info(info)
            
        self.__log()

    def __log(self):
        items = ["{0}\n"
                 "  Fullpath:               {1}\n"
                 "  Seekable:               {2}\n"
                 "  Video:                  {3}\n".format(self.filename, self.fullpath, self.seekable, self.is_video)]
        
        if self.is_video:
            items.append("    Dimension(pixel):     {0}\n"
                         "    Dimension(display):   {1}\n"
                         "    Aspect(display):      {2}\n"
                         "    Subtitles:            {3}\n".format(
                    "{0[0]}x{0[1]}".format(self.original_dimension),
                    "{0[0]}x{0[1]}".format(self.scaled_dimension),
                    float(self.scaled_dimension[2]),
                    self.subtitle_had))
        logging.debug(''.join(items))

    def __gen_meta_info(self,info):
        self.filename = info["ID_FILENAME"]
        
        self.fullpath = os.path.realpath(self.filename)

        self.seekable = (info["ID_SEEKABLE"] == "1")
        self.is_video = True if "ID_VIDEO_ID" in info else False
        
    def __gen_video_info(self,info):
        self.original_dimension[0] = int(info["ID_VIDEO_WIDTH"])
        self.original_dimension[1] = int(info["ID_VIDEO_HEIGHT"])
        if "ID_VIDEO_ASPECT" in info:
            self.original_dimension[2] = Fraction(info["ID_VIDEO_ASPECT"]).limit_denominator(100)
        self.scaled_dimension = self.original_dimension[:]

        # amend the dim params
        if self.scaled_dimension[2] == 0:
            self.scaled_dimension[2] =  Fraction(self.scaled_dimension[0],self.scaled_dimension[1])

        # fix video width for non-square pixel, i.e. w/h != aspect
        # or the video expanding will not work
        if abs(Fraction(self.scaled_dimension[0],self.scaled_dimension[1]) - self.scaled_dimension[2]) > 0.1:
            self.scaled_dimension[0] = int(round(self.scaled_dimension[1] * self.scaled_dimension[2]))

    def __gen_subtitle_info(self,info):
        if "ID_SUBTITLE_ID" in info:
            self.subtitle_had = "embedded text"
        if "ID_FILE_SUB_ID" in info:
            self.subtitle_had = "external text"
        if "ID_VOBSUB_ID" in info:
            self.subtitle_had = "external vobsub"

class CmdLineParser:
    """ Attributes:
      application
      files
      args
      bad_args
    """
    def __init__(self, args, mplayer):
        self.__args = args[:]
        self.files = []
        self.args = []
        self.bad_args =[]

        a = os.path.basename(self.__args.pop(0))
        if "mplayer" in a:
            self.application = "player"
        elif "midentify" in a:
            self.application = "identifier"
            self.args = self.__args
            self.__args = []
        else:
            self.application = "unknown"

        while len(self.__args)>0:
            s = self.__args.pop(0)
            if s == "-debug":
                logging.root.setLevel(logging.DEBUG)
            elif s == "-dry-run":
                logging.root.setLevel(logging.DEBUG)
                global dry_run
                dry_run = True
            elif s == "--":
                self.files.extend(self.__args)
                self.__args = []
            elif s.startswith("-"):
                flag = mplayer.support(s.split('-',1)[1])
                if flag == 0:
                    self.bad_args.append(s)
                elif flag == 1:
                    self.args.append(s)
                elif flag == 2:
                    self.args.append(s)
                    if len(self.__args)>0:
                        self.args.append(self.__args.pop(0))
            else:
                self.files.append(s)

# http://www.python.org/dev/peps/pep-0318/
def singleton(cls):
    instances = {}
    def getinstance():
        if cls not in instances:
            instances[cls] = cls()
        return instances[cls]
    return getinstance

@singleton
class Fifo:
    def __init__(self):
        import tempfile
        self.__tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.__tmpdir, "mplayer_fifo")
        self.args = "-input file={0}".format(self.path).split()
        os.mkfifo(self.path)
    def __del__(self):
        os.unlink(self.path)
        os.rmdir(self.__tmpdir)

def run():
    fifo = Fifo()
    mplayer = MPlayer(fifo)
    parser = CmdLineParser(sys.argv,mplayer)

    if parser.application == "identifier":
        print '\n'.join(mplayer.identify(parser.args))
        return

    if parser.application == "player":
        screen_dim = check_dimension()

        playlist = parser.files
        if len(parser.files) == 1:
            playlist = genlist(parser.files[0])

        if len(playlist) == 0:
            mplayer.play(parser.args)
        for f in playlist:
            media = Media(f, mplayer)
            args = parser.args

            if not media.exist:
                logging.debug("{0} does not exist".format(f))

            proc_fetch = None
            cmd_conn_read_end = None
            if media.is_video:
                # todo: what if -noass specified?
                if "-noass" in parser.args:
                    args.extend(expand_video(media, "noass", screen_dim[2]))
                else:
                    args.extend(expand_video(media, "ass", screen_dim[2]))

                cmd_conn_read_end, cmd_conn_write_end = multiprocessing.Pipe(False)

                if not media.subtitle_had.endswith("text"):
                    proc_fetch = multiprocessing.Process(target=handle_shooter_subtitles, args=(media, cmd_conn_write_end))
                    proc_fetch.start()
            
            mplayer.play(args+[f], cmd_conn_read_end)
            if proc_fetch and proc_fetch.is_alive():
                logging.info("Terminating subtitle fetching...")
                proc_fetch.terminate()

            if mplayer.last_exit_status == "Quit":
                break

if __name__ == "__main__":
    dry_run = False
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

    if sys.hexversion < 0x02060000:
        logging.info("Please run the script with python>=2.6.0")
    else:
        run()
