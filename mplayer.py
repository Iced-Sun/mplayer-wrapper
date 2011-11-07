#!/usr/bin/env python
#
# Copyright 2010,2011 Bing Sun <subi.the.dream.walker@gmail.com> 
# Time-stamp: <subi 2011/11/07 13:30:04>
#
# mplayer-wrapper is a simple frontend for MPlayer written in Python,
# trying to be a transparent interface. It is convenient to rename the
# script to "mplayer" and place it in your $PATH (don't overwrite the
# real MPlayer); you would not even notice its existence.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
# USA

import os, sys, threading, logging
import struct, urllib2
from subprocess import *
from fractions import Fraction

def which(cmd):
    """Mimic the shell command "which".
    """
    def exefy(fullpath):
        return fullpath if os.path.exists(fullpath) and os.access(fullpath, os.X_OK) else None

    pdir = os.path.split(cmd)[0]
    if pdir:
        fullpath = exefy(cmd)
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            fullpath = exefy(os.path.join(path,cmd))
            if fullpath: break
    return fullpath

def expand_video(m, method = "ass", target_aspect = Fraction(4,3)):
    # scale to video width which is never modified
    args = "-subfont-autoscale 2"

    if method == "none":
        args = ""
    elif m.scaled_dimension[2] < Fraction(4,3) :
        args = "-vf-pre dsize=4/3"
    elif method == "ass":
        # ass is total mess
        # 3 aspects: video, screen, and PlayResX:PlayResY
        # mplayer use PlayResX = 336 as default, so do we

        # magic values
        # ass dimesion magic
        magic_ass_dim = [336,252,Fraction(336,252)]
        magic_font_scale = 1.3
        magic_subtitle_height = int(magic_font_scale * 50)

        # basic scale factor
        m2t = m.scaled_dimension[2] / target_aspect

        # base opts
        args += " -ass -ass-font-scale {0}".format(magic_font_scale*m2t)

        # margin opts
        margin = (m2t - 1) * m.scaled_dimension[1] / 2
        if margin > 0:
            args += " -ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin))
            args += " -ass-force-style "
            args += "PlayResX={0[0]},PlayResY={0[1]}".format(magic_ass_dim)
            args += ",ScaleX={0},ScaleY=1".format(1/float(m2t))
            # place the subtitle as close to the picture as possible
            offset = margin-magic_subtitle_height
            if offset < 0: offset = 0
            args += ",MarginV={0}".format(float(offset*magic_ass_dim[1]/(m.scaled_dimension[0]/m.scaled_dimension[2])))
    else:
        # -vf expand does its own non-square pixel adjustment
        args += " -subpos 98 -vf-pre expand={0}::::1:{1}".format(m.original_dimension[0],target_aspect)
    return args.split()

class SubFetcher:
    """Reference: http://code.google.com/p/sevenever/source/browse/trunk/misc/fetchsub.py
    """
    subtitles = []
    save_dir = ""

    def do(self):
        self.fetch()
        self.save()
        self.activate_in_mplayer()
        
    def __init__(self, m):
        self.save_dir = os.path.splitext(m.fullpath)[0]
        self.__build_data(m)
        
        self.req = urllib2.Request(self.url)
        for h in self.header: self.req.add_header(h[0],h[1])
        self.req.add_data(self.data)

    def fetch(self):
        def parse_package(response):
            c = response.read(8)
            package_length, desc_length = struct.unpack("!II", c)
            description = response.read(desc_length).decode("UTF-8")

            logging.info("Length of current package in bytes: {0}".format(package_length))
            
            c = response.read(5)
            package_length, file_count = struct.unpack("!IB", c)

            logging.info("{0} subtitles in current package ({1})".format(file_count,description))

            for j in range(file_count):
                parse_file(response)

        def parse_file(response):
            c = response.read(8)
            filepack_length, ext_length = struct.unpack("!II", c)

            file_ext = response.read(ext_length)#.decode("UTF-8")
                
            c = response.read(4)
            file_length = struct.unpack("!I", c)[0]
            subtitle = response.read(file_length)
            if subtitle.startswith("\x1f\x8b"):
                import gzip
                from cStringIO import StringIO
                self.subtitles.append([file_ext, gzip.GzipFile(fileobj=StringIO(subtitle)).read()])
            else:
                logging.warning("Unknown format in downloaded subtiltle data.")

        # now request the real connection
        response = urllib2.urlopen(self.req)

        c = response.read(1)
        package_count = struct.unpack("!b", c)[0]

        logging.info("{0} subtitle packages found".format(package_count))

        for i in range(package_count):
            parse_package(response)
        logging.info("{0} subtitle(s) fetched.".format(len(self.subtitles)))

        for i in range(len(self.subtitles)):
            if i==0:
                suffix = ""
            else:
                suffix = str(i)
            self.subtitles[i][0] = self.save_dir + suffix + '.' + self.subtitles[i][0]

    def save(self):
        enca = which("enca")
        for sub in self.subtitles:
            logging.info("Saving subtitle file {0}".format(sub[0]))
            f = open(sub[0],"wb")
            f.write(sub[1])
            f.close()
            if enca != None:
                logging.info("Convert {0} to UTF-8".format(sub[0]))
                Popen([enca]+"-c -x utf8 -L zh".split()+[sub[0]]).communicate()

    def activate_in_mplayer(self):
        for sub in self.subtitles:
            MPlayer.cmd("sub_load {0}".format(sub[0]))
        MPlayer.cmd("sub_file 0")

    def __build_data(self,m):
        def hashing(path):
            sz = os.path.getsize(path)
            if sz>8192:
                import hashlib
                f = open(path, 'rb')
                filehash = ';'.join(map(lambda s: (f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1], (lambda l:[4096, l/3*2, l/3, l-8192])(sz)))
                f.close()
            else:
                filehash = ""
            return filehash

        schemas = ["http", "https"]
        # [www, svlayer, splayer5].shooter.cn has issues
        servers = ["splayer1", "splayer2", "splayer3", "splayer4"]

        import random
        post_boundary = "----------------------------{0:x}".format(random.getrandbits(48))

        self.url = "{0}://{1}.shooter.cn/api/subapi.php".format(random.choice(schemas), random.choice(servers))

        self.header = []
        self.header.append(["User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543))])
        self.header.append(["Content-Type", "multipart/form-data; boundary={0}".format(post_boundary)])

        data = []
        data.append(["pathinfo", os.path.join("c:/",m.dirname,m.basename)])
        data.append(["filehash", hashing(m.fullpath)])
#        data.append(["lang", "chn"])
        self.data = ""
        for d in data:
            self.data += """--{0}
Content-Disposition: form-data; name="{1}"

{2}
""".format(post_boundary, d[0], d[1])
        self.data += "--" + post_boundary + "--"

        logging.debug("""Will query server {0} with
{1}
{2}
""".format(self.url,self.header,self.data))
        
class Fifo:
    path = ""
    args = []
    __tmpdir = ""
    __initialized = False
    def __init__(self):
        if not Fifo.__initialized:
            import tempfile
            Fifo.__tmpdir = tempfile.mkdtemp()
            Fifo.path = os.path.join(Fifo.__tmpdir, 'mplayer_fifo')
            os.mkfifo(Fifo.path)
            Fifo.args = "-input file={0}".format(Fifo.path).split()
            Fifo.__initialized = True
    def __del__(self):
        if Fifo.__initialized:
            os.unlink(Fifo.path)
            os.rmdir(Fifo.__tmpdir)
            Fifo.path = ""
            Fifo.args = []
            Fifo.__initialized = False
    
class MPlayer:
    # TODO: "not compiled in option"
    def __init__(self):
        if not MPlayer.initialized:
            MPlayer.probe_mplayer()
            MPlayer.query_supported_opts()
            MPlayer.initialized = True;

    @staticmethod
    def support(opt):
        support = False
        take_param = False
        if opt in MPlayer.supported_opts:
            support = True
            take_param = MPlayer.supported_opts[opt]
        return [support, take_param]

    @staticmethod
    def identify(filelist=[]):
        result = []
        p = Popen([MPlayer.path]+"-vo null -ao null -frames 0 -identify".split()+filelist, stdout=PIPE, stderr=PIPE)
        for line in p.communicate()[0].splitlines():
            if line.startswith("ID_"): result.append(line)
        return result

    @staticmethod
    def cmd(cmd_string):
        if MPlayer.instance.poll() == None:
            logging.debug("Sending command <{0}> to <{1}>".format(cmd_string, Fifo.path))
            fifo = open(Fifo.path,"w")
            fifo.write(cmd_string+'\n')
            fifo.close()
        
    @staticmethod
    def play(args=[],timers=[]):
        for t in timers: t.start()
        p = MPlayer.instance = Popen([MPlayer.path]+args,stdin=sys.stdin,stdout=PIPE,stderr=None)
        MPlayer.tee()
        for t in timers: t.cancel(); t.join()

    @staticmethod
    def tee(f=sys.stdout):
        p = MPlayer.instance
        line = ""
        while p.poll() == None:
            c = p.stdout.read(1)
            if c == '\r':
                d = p.stdout.read(1)
                if d == '\n':
                    line += '\r\n'
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    line = ""
                else:
                    line += '\r'
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    line = d
            else:
                line += c
        sys.stdout.write(line)
        
    ## internal
    initialized = False
    instance = None
    path = ""
    supported_opts = {}

    @staticmethod
    def probe_mplayer():
        for p in ["/opt/bin/mplayer","/usr/local/bin/mplayer","/usr/bin/mplayer"]:
            if os.path.isfile(p):
                MPlayer.path = p
        if MPlayer.path == "":
            raise RuntimeError,"Didn't find a mplayer binary."

    @staticmethod
    def query_supported_opts():
        for line in Popen([MPlayer.path, "-list-options"], stdout=PIPE).communicate()[0].splitlines():
            s = line.split();
            if len(s) < 7:
                continue
            if s[len(s)-1] == "Yes" or s[len(s)-1] == "No":
                opt = s[0].split(":") # take care of option:suboption
                if opt[0] in MPlayer.supported_opts:
                    continue
                if len(opt) == 2:
                    take_param = True
                elif s[1] != "Flag":
                    take_param = True
                else:
                    take_param = False
                MPlayer.supported_opts[opt[0]] = take_param
        # handle vf* af*: mplayer reports option name as vf*, while it
        # is a family of options.
        del MPlayer.supported_opts['af*']
        del MPlayer.supported_opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            MPlayer.supported_opts[extra] = True
        for extra in ["af-clr","vf-clr"]:
            MPlayer.supported_opts[extra] = False

class Media:
    """Construct media metadata by midentify; may apply "proper" fixes
    """
    exist = True

    filename = ""
    fullpath = ""
    
    basename = ""
    dirname = "" 
    name = ""
   
    seekable = True
    is_video = False

    original_dimension = [0,0,Fraction(0)]
    scaled_dimension = [0,0,Fraction(0)]

    subtitle_had = "none"
    subtitle_need_fetch = False

    def __init__(self,info_input):
        """Parse the output by midentify.
        """
        info = {}
        for l in info_input:
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
        log_string = """{0}
Path:
  Fullpath:             {1}
  Base name:            {2}
  Dir name:             {3}
Seekable:               {4}
Video:                  {5}""".format(self.filename,
                                      self.fullpath,
                                      self.basename,
                                      self.dirname,
                                      self.seekable,
                                      self.is_video)
        if self.is_video:
            log_string += """
  Dimension(pixel):     {0}
  Dimension(display):   {1}
  Aspect(display):      {2}
  Subtitles:            {3}
    Need Fetch:         {4}
""".format("{0[0]}x{0[1]}".format(self.original_dimension),
           "{0[0]}x{0[1]}".format(self.scaled_dimension),
           float(self.scaled_dimension[2]),
           self.subtitle_had,
           self.subtitle_need_fetch)
        logging.debug(log_string)

    def __gen_meta_info(self,info):
        self.filename = info["ID_FILENAME"]
        
        self.fullpath = os.path.realpath(self.filename)
        self.basename = os.path.basename(self.fullpath)
        self.dirname = os.path.basename(os.path.dirname(self.fullpath))
        self.name,ext = os.path.splitext(self.basename)

        self.seekable = (info["ID_SEEKABLE"] == "1")
        if "ID_VIDEO_ID" in info:
            self.is_video = True
        else:
            self.is_video = False
        
    def __gen_video_info(self,info):
        self.original_dimension[0] = int(info["ID_VIDEO_WIDTH"])
        self.original_dimension[1] = int(info["ID_VIDEO_HEIGHT"])
        if "ID_VIDEO_ASPECT" in info:
            self.original_dimension[2] = Fraction(info["ID_VIDEO_ASPECT"])
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
        if "text" in self.subtitle_had:
            self.subtitle_need_fetch = False
        else:
            self.subtitle_need_fetch = True

class Launcher:
    """Command line parser and executor.
    """
    def run(self):
        if Launcher.meta.role == "identifier":
            print '\n'.join(MPlayer.identify(Laucher.meta.left_opts))
        else:
            for f in Launcher.meta.files:
                m = Media(MPlayer.identify([f]))
                hooks = []
                
                if m.exist:
                    args = Fifo.args
                    if m.is_video:
                        args += expand_video(m, Launcher.meta.expand, Launcher.meta.screen_dim[2])
                        sub = SubFetcher(m)
                        if dry_run == False and m.subtitle_need_fetch == True:
                            hooks.append(threading.Timer(5.0, sub.do))

                    args += Launcher.meta.opts+[f]
                    logging.debug("Final args:\n{0}".format(' '.join(args)))
                    if dry_run == False:
                        MPlayer.play(args,hooks)
                else:
                    logging.info("{0} does not exist".format(f))
                
    class Meta:
        # features
        role = "player"
        expand = "ass"
        resume = True
        continuous = True
        # meta-info
        screen_dim = []
        opts = []
        invalid_opts = []
        left_opts = []
        files = []

    def __init__(self):
        def check_role(path):
            a = os.path.basename(path)
            if "mplayer" in a:
                role = "player"
            elif "midentify" in a:
                role = "identifier"
            else:
                role = "unknown"
            return role
            
        def check_dimension():
            """Select the availible maximal screen dimension by xrandr.
            """
            dim = [640, 480, Fraction(640,480)]
            if which("xrandr") != None:
                p1 = Popen(["xrandr"], stdout = PIPE)
                p2 = Popen(["grep", "*"], stdin = p1.stdout, stdout = PIPE)
                p1.stdout.close()
                for line in p2.communicate()[0].splitlines():
                    t =  line.split()[0].split('x')
                    if int(t[0]) > dim[0]:
                        dim[0] = int(t[0])
                        dim[1] = int(t[1])
                dim[2] = Fraction(dim[0],dim[1])
            return dim

        def parse_args(meta):
            while len(meta.left_opts)>0:
                a = meta.left_opts.pop(0)
                if a == "-debug":
                    logging.root.setLevel(logging.DEBUG)
                elif a == "-dry-run":
                    logging.root.setLevel(logging.DEBUG)
                    global dry_run
                    dry_run = True
                elif a == "-noass":
                    meta.expand = "noass"
                    meta.opts.append(a)
                elif a == "--":
                    meta.files += self.__meta.left_opts
                    meta.left_opts = []
                elif a[0] == "-":
                    f = MPlayer.support(a.split('-',1)[1])
                    if f[0] == True:
                        meta.opts.append(a)
                        if f[1] == True and len(meta.left_opts)>0:
                            meta.opts.append(meta.left_opts.pop(0))
                        else:
                            # option not supported by mplayer, silently ignore it
                            meta.invalid_opts.append(a)
                else:
                    meta.files.append(a)
            if len(meta.files) != 1:
                meta.continuous = False

        def info(meta):
            log_string = "Run as <{0}>".format(meta.role)
            if meta.role == "player":
                log_string += """
Command line options:
  Unpassed:             {0}
  Bypassed:             {1}
  Discarded:            {2}
Playlist:
  {3}
Screen:
  Dimension:            {4}
  Aspect:               {5}
Features:
  Video expanding:      {6}
  Resume player:        {7}
  Continuous playing:   {8}
""".format(' '.join(meta.left_opts),
           ' '.join(meta.opts),
           ' '.join(meta.invalid_opts),
           '\n  '.join(meta.files),
           "{0[0]}x{0[1]}".format(meta.screen_dim),
           "{0.numerator}:{0.denominator}".format(meta.screen_dim[2]),
           meta.expand,
           meta.resume,
           meta.continuous)

            logging.debug(log_string)

        # init Launcher meta infos
        Launcher.meta = Launcher.Meta()
        Launcher.meta.role = check_role(sys.argv.pop(0))
        Launcher.meta.left_opts = sys.argv

        if Launcher.meta.role != "identifier":
            Launcher.meta.screen_dim = check_dimension()
            parse_args(Launcher.meta)

        info(Launcher.meta)

# main
dry_run = False
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

dump_f = Fifo()
MPlayer()
Launcher().run()
