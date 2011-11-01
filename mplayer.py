#!/usr/bin/env python
#
# Copyright 2010,2011 Bing Sun <subi.the.dream.walker@gmail.com> 
# Time-stamp: <subi 2011/11/01 20:27:59>
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

import os,sys
from subprocess import *
from fractions import Fraction

def which(program):
    """Mimic the shell command "which".
    """
    import os
    def is_exe(fpath):
        return os.path.exists(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file
    return None

def check_dimension():
    """Select the availible maximal screen dimension by xrandr.
    """
    dim = [640,480,Fraction(640,480)]
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

def expand_video(m, expand_method = "ass", target_aspect = Fraction(4,3)):
    # scale to video width which is never been touched
    args = "-subfont-autoscale 2"

    if expand_method == "none":
        args = ""
    elif m.scaled_dimension[2] < Fraction(4,3) :
        args = "-vf-pre dsize=4/3"
    elif expand_method == "ass":
        # ass is total mess
        # 3 aspects: video, screen, and PlayResX:PlayResY
        # mplayer use PlayResX = 336 as default, so do we
        ass_dim = [336,252,Fraction(336,252)]

        # basic scale factor
        m2t = m.scaled_dimension[2] / target_aspect

        # base opts
        args += " -ass -embeddedfonts"
        args += " -ass-font-scale {0}".format(1.4*m2t)

        # margin opts
        margin = (m2t - 1) * m.scaled_dimension[1] / 2
        if margin > 0:
            args += " -ass-use-margins -ass-bottom-margin {0} -ass-top-margin {0}".format(int(margin))
            args += " -ass-force-style "
            args += "PlayResX={0[0]},PlayResY={0[1]}".format(ass_dim)
            args += ",ScaleX={0},ScaleY=1".format(1/float(m2t))
            # put the subtitle as close to video picture as possible
            offset = margin-70
            if offset < 0:
                offset = 0
            args += ",MarginV={0}".format(float(offset*ass_dim[1]/(m.scaled_dimension[0]/m.scaled_dimension[2])))
    else:
        # -vf expand does its own non-square pixel adjustment
        args += " -subpos 98 -vf-pre expand={0}::::1:{1}".format(m.original_dimension[0],target_aspect)
    return args.split()

def fetch_subtitle(m):
    """
    Reference: http://code.google.com/p/sevenever/source/browse/trunk/misc/fetchsub.py
    """
    subtitles = []
    if "text" in m.subtitle_had:
        return subtitles
    
    import random, urllib2
    post_boundary = "----------------------------{0:x}".format(random.getrandbits(48))

    req = urllib2.Request("{0}://{1}.shooter.cn/api/subapi.php".format(
            random.choice(["http","https"]),
            random.choice(["www", "svlayer", "splayer1", "splayer2", "splayer3", "splayer4", "splayer5"])))
    req.add_header("User-Agent", "SPlayer Build ${0}".format(random.randint(1217,1543)))
    req.add_header("Content-Type", "multipart/form-data; boundary={0}".format(post_boundary))

    data = ""
    for item in [
        ["pathinfo", os.path.join("c:/",m.dirname,m.basename)],
        ["filehash", m.shooter_hash_string],
        ["lang", "chn"]
        ]:
        data += """--{0}
Content-Disposition: form-data; name="{1}"

{2}
""".format(post_boundary, item[0], item[1])

    data = data + "--" + post_boundary + "--"
    req.add_data(data)

    if debug:
        print 
        print "==== Will post data ===="
        print req.get_full_url()
        print req.get_data()
        return subtitles

    response = urllib2.urlopen(req)

    # parse response
    import struct
    from cStringIO import StringIO

    c = response.read(1)
    package_count = struct.unpack("!b", c)[0]

#        if debug:
    print "  {0} subtitle packages".format(package_count)

    for dumb_i in range(package_count):
        c = response.read(8)
        package_length, desc_length = struct.unpack("!II", c)
        description = response.read(desc_length).decode("UTF-8")
            
        c = response.read(5)
        package_length, file_count = struct.unpack("!IB", c)

#            if debug:
        print "    {0} subtitles in package {1}({2})".format(file_count,dumb_i+1,description)

        for dumb_j in range(file_count):
            c = response.read(8)
            filepack_length, ext_length = struct.unpack("!II", c)

            file_ext = response.read(ext_length).decode("UTF-8")
                
            c = response.read(4)
            file_length = struct.unpack("!I", c)[0]
            subtitle = response.read(file_length)
            if subtitle.startswith("\x1f\x8b"):
                import gzip
                subtitles.append([dumb_i, dumb_j, file_ext, gzip.GzipFile(fileobj=StringIO(subtitle)).read()])
            else:
                print "Unknown package format in downloaded subtiltle data."
                
    return subtitles
    
class MPlayer:
    """mplayer execution envirionment and delegation.

    Usage:
    MPlayer mp;
    mp.check_opt("vo");
    mp.identify("my.avi");
    mp("my.avi");
    """
    # TODO: "not compiled in option"
    def check_opt(self, opt):
        if len(self.__opts) == 0:
            self.__get_opts()
        support = False
        take_param = False
        if opt in self.__opts:
            support = True
            take_param = self.__opts[opt]
        return [support, take_param]

    def identify(self,fl=[]):
        p = self(self.__identify_args + fl, True)
        p2 = Popen(self.__grep, stdin = p.stdout, stdout = PIPE)
        return p2.communicate()[0]

    def play(self,args=[],f=[]):
        if debug:
            print "==== Will execute ===="
            print ' '.join([self.__path]+args+f)
        else:
            p = self(args + f)
            while p.poll() == None:
                o = p.stdout.readline()
                sys.stdout.write(o)
   
    def __call__(self,args=[], suppress_stderr = False):
        
        if suppress_stderr:
            return Popen([self.__path] + args, stdin = sys.stdin, stdout = PIPE, stderr = PIPE)
        else:
            return Popen([self.__path] + args, stdin = sys.stdin, stdout = PIPE, stderr = None)
            
    # internal
    __path = ""
    __opts = {}
    __identify_args = "-vo null -ao null -frames 0 -identify".split()
    __grep = """grep ^ID_.*= """.split()
    
    def __init__(self):
        self.__probe_mplayer()

    def __probe_mplayer(self):
        for p in ["/opt/bin/mplayer","/usr/local/bin/mplayer","/usr/bin/mplayer"]:
            if os.path.isfile(p):
                self.__path = p
        if self.__path == "":
            raise RuntimeError,"Didn't find a mplayer binary."

    def __get_opts(self):
        for line in Popen([self.__path, "-list-options"], stdout=PIPE).communicate()[0].splitlines():
            s = line.split();
            if len(s) < 7:
                continue
            if s[len(s)-1] == "Yes" or s[len(s)-1] == "No":
                opt = s[0].split(":") # take care of option:suboption
                if opt[0] in self.__opts:
                    continue
                if len(opt) == 2:
                    take_param = True
                elif s[1] != "Flag":
                    take_param = True
                else:
                    take_param = False
                self.__opts[opt[0]] = take_param
        # handle vf* af*: mplayer reports option name as vf*, while it
        # is a family of options.
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            self.__opts[extra] = True
        for extra in ["af-clr","vf-clr"]:
            self.__opts[extra] = False

class Media:
    """Construct media metadata by the result of midentify; may apply some "proper" fixes
    """
    exist = True

    name = ""
    fullpath = ""
    basename = ""
    dirname = ""
    
    seekable = True
    is_video = False

    original_dimension = [0,0,Fraction(0)]
    scaled_dimension = [0,0,Fraction(0)]

    subtitle_had = "none"

    shooter_hash_string = ""
    
    def info(self):
        print
        print "==== Media info begin ===="
        print "  Media: ",                   self.name
        print "    Fullpath: ",              self.fullpath
        print "    Base name: ",             self.basename
        print "    Dir name: ",              self.dirname
        print "  Seekable: ",                self.seekable
        print "  Has video: ",               self.is_video
        if self.is_video:
            print "    Dimension: ",         "{0[0]}x{0[1]}".format(self.scaled_dimension)
            print "    Aspect: ",            float(self.scaled_dimension[2])
            print "    Subtitles: ",         self.subtitle_had
            print "    File hash: ",         self.shooter_hash_string
            print "    Subtitles: ",         self.subtitle_had
        print "====  Media info end  ===="
        print

    def __init__(self,info_input):
        """Parse the output by midentify.
        """
        info = {}
        for l in info_input.splitlines():
            a = l.split('=')
            info[a[0]] = a[1]

        if not "ID_FILENAME" in info:
            self.exist = False;
            return

        self.__gen_meta_info(info)
        
        if self.is_video:
            self.__gen_video_info(info)
            self.__gen_subtitle_info(info)
            self.__gen_shooter_info(info)

        if debug:
            self.info()

    def __gen_meta_info(self,info):
        self.name = info["ID_FILENAME"]
        
        self.fullpath = os.path.realpath(self.name)
        self.basename = os.path.basename(self.fullpath)
        self.dirname = os.path.basename(os.path.dirname(self.fullpath))
        
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
    def __gen_shooter_info(self,info):
        sz = os.path.getsize(self.fullpath)
        if sz>8192:
            import hashlib
            f = open(self.fullpath, 'rb')
            self.shooter_hash_string = ';'.join(map(lambda s: (f.seek(s), hashlib.md5(f.read(4096)).hexdigest())[1], (lambda l:[4096, l/3*2, l/3, l-8192])(sz)))
            f.close()

class Launcher:
    """Command line parser and executor.
    """
    def run(self):
        if self.__meta.role == "identifier":
            print mplayer.identify(self.__meta.left_opts)
        else:
            for f in self.__meta.files:
                m = Media(mplayer.identify([f]))
                args = []
                if m.is_video:
                    args += expand_video(m, self.__meta.expand, self.__meta.screen_dim[2])
                if m.exist:
                    import threading
                    t_mplayer = threading.Thread(target=mplayer.play, args=(args+self.__meta.opts,[f]))
                    t_mplayer.start()

                    if debug:
                        fetch_subtitle(m)
                    else:
                        import time
                        sleep_time = 0
                        while t_mplayer.is_alive() and sleep_time < 5:
                            time.sleep(0.05)
                            sleep_time += 0.05
                        if sleep_time >= 5:
                            fetch_subtitle(m)
                
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
        def info(self):
            print "==== Launcher info begin ===="
            print "  Role: ",                 self.role
            if self.role == "player":
                print "  Command line options: "
                if len(self.left_opts) > 0:
                    print "    Unpassed: ",   ' '.join(self.left_opts)
                if len(self.opts) > 0:
                    print "    Bypassed:",    ' '.join(self.opts)
                if len(self.invalid_opts) > 0:
                    print "    Discarded: ",  ' '.join(self.invalid_opts)
                if len(self.files) > 0:
                    print "  Play list: "
                    for f in self.files:
                        print "    ",f
                print "  Sreen dimension: ",              "{0[0]}x{0[1]}".format(self.screen_dim)
                print "  Sreen aspect: ",                 "{0.numerator}:{0.denominator}".format(self.screen_dim[2])
                print "  Global expanding method: ",      self.expand
                print "  Resume last played position: ",  self.resume
                print "  Automatic continuous play: ",    self.continuous
            print "==== Launcher info end ===="

    __meta = Meta()

    def __init__(self):
        self.__check_action()
        self.__meta.left_opts = sys.argv
        if self.__meta.role != "identifier":
            self.__meta.screen_dim = check_dimension()
            self.__parse_args()
        if debug:
            self.__meta.info()
            
    def __check_action(self):
        a = os.path.basename(sys.argv.pop(0))
        if a == "mplayer":
            self.__meta.role = "player"
        elif a == "midentify":
            self.__meta.role = "identifier"
            
    def __parse_args(self):
        while len(self.__meta.left_opts)>0 :
            a = self.__meta.left_opts.pop(0)

            if a == "-debug":
                global debug
                debug = True
            elif a == "-noass":
                self.__meta.expand = "noass"
                self.__meta.opts.append(a)
            elif a == "--":
                self.__meta.files += self.__meta.left_opts
                self.__meta.left_opts = []
            elif a[0] == "-":
                f = mplayer.check_opt(a.split('-',1)[1])
                if f[0] == True:
                    self.__meta.opts.append(a)
                    if f[1] == True and len(self.__meta.left_opts)>0:
                        self.__meta.opts.append(self.__meta.left_opts.pop(0))
                else:
                    # option not supported by mplayer, silently ignore it
                    self.__meta.invalid_opts.append(a)
            else:
                self.__meta.files.append(a)

        if len(self.__meta.files) != 1:
            self.__meta.continuous = False

# main
debug = False
mplayer = MPlayer()
Launcher().run()


