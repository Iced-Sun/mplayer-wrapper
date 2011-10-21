#!/usr/bin/env python
# Copyright 2010,2011 Bing Sun <subi.the.dream.walker@gmail.com> 
# Time-stamp: <subi 2011/10/21 13:29:23>
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
    dim = [640,480]
    if which("xrandr") != None:
        p1 = Popen(["xrandr"], stdout = PIPE)
        p2 = Popen(["grep", "*"], stdin = p1.stdout, stdout = PIPE)
        p1.stdout.close()
        for line in p2.communicate()[0].splitlines():
            t =  line.split()[0].split('x')
            if int(t[0]) > dim[0]:
                dim[0] = int(t[0])
                dim[1] = int(t[1])
    return dim

class MPlayer:
    """mplayer execution delegation class.

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
        import shlex
        p = self("-vo null -ao null -frames 0 -identify".split() + fl)
        args2 = shlex.split(""" sed -ne '/^ID_.*=/ {s/[]()|&;<>`'"'"'\\!$" []/\\&/g;p}' """)
        p2 = Popen(args2, stdin = p.stdout, stdout = PIPE)
        return p2.communicate()[0]

    def __call__(self,args=[]):
        return Popen([self.__path] + args, stdout = PIPE)
            
    # internal
    __path = ""
    __opts = {}
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
#                key = "-" + opt[0]
                if opt[0] in self.__opts:
                    continue
                if len(opt) == 2:
                    take_param = True
                elif s[1] != "Flag":
                    take_param = True
                else:
                    take_param = False
                self.__opts[opt[0]] = take_param
        # handel vf* af*
        del self.__opts['af*']
        del self.__opts['vf*']
        for extra in ["af","af-adv","af-add","af-pre","af-del","vf","vf-add","vf-pre","vf-del"]:
            self.__opts[extra] = True
        for extra in ["af-clr","vf-clr"]:
            self.__opts[extra] = False

class Media:
    """Media metadata and corresponding mplayer params.
    """
    width = 0
    height = 0
    aspect = 0

    __meta = {}
    __opts = []
    def __init__(self,info):
        for l in info.splitlines():
            a = l.split('=')
            self.__meta[a[0]] = a[1]
        self.width = int(self.__meta["ID_VIDEO_WIDTH"])
        self.height = int(self.__meta["ID_VIDEO_HEIGHT"])
        self.aspect = float(self.__meta["ID_VIDEO_ASPECT"])
        # if (( video_aspect < 1.333 )), force 4:3
        if self.width/self.height < 4/3 :
            self.__suggested_expand = "dsize"
        self.__expand_video()

    def __expand_video(self):
        # fix ID_VIDEO_WIDTH of non-square pixel, i.e.
        # if width != height * aspect, let width = height * aspect
        if abs(self.width - self.height * self.aspect > 10):
            self.width = self.height * self.aspect
#        "-ass -embeddedfonts"
        
class Launcher:
    """Command line parser and executor.
    """
    def run(self):
        if self.__meta.role == "identifier":
            print self.__mplayer.identify(self.__meta.left_opts)
        else:
            print
            for f in self.__meta.files:
                m = Media(self.__mplayer.identify([f]))
                
    class Meta:
        debug = False
        role = "player"
        expand = "ass"
        resume = True
        dim = None
        opts = []
        invalid_opts = []
        left_opts = []
        files = []
        continuous = True
        def info(self):
            print "Extra info begin ==============================="
            print "  Role: ",self.role
            print "  Unpassed options: ",self.left_opts
            print "  Bypassed options: ",self.opts
            print "  Invalid options: ",self.invalid_opts
            print "  Screen dimension: ",self.dim
            print "  Play list: ",self.files
            print "Extra info end ================================="
            print

    __meta = Meta()
    __mplayer = None

    def __init__(self,player=MPlayer):
        self.__mplayer = player
        self.__check_action()
        self.__meta.left_opts = sys.argv
        if self.__meta.role != "identifier":
            self.__parse_args()
            self.__meta.dim = check_dimension()
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
                self.__meta.debug = True
            elif a == "--":
                self.__meta.files += self.__meta.left_opts
                self.__meta.left_opts = []
            elif a[0] == "-":
                f = self.__mplayer.check_opt(a.split('-',1)[1])
                if f[0] == True:
                    self.__meta.opts.append(a)
                    if f[1] == True and len(self.__meta.left_opts)>0:
                        self.__meta.opts.append(self.__meta.left_opts.pop(0))
                else:
                    print "The option '{0}' is not supported by mplayer. Silently ignore it.".format(a)
                    self.__meta.invalid_opts.append(a)
            else:
                self.__meta.files.append(a)

        if len(self.__meta.files) != 1:
            self.__meta.continuous = False

player = MPlayer()

Launcher(player).run()
