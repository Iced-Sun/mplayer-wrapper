#!/usr/bin/env python2
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-01-12 16:32:01 by subi>

def check_screen_dim():
    '''Select the maximal available screen dimension.
    '''
    dim = Dimension()
    dim_updated = False
    
    if not which('xrandr'):
        return dim

    for l in subprocess.check_output(['xrandr']).splitlines():
        if l.startswith('*'): # xrandr 1.1
            _,w,_,h = l.split()
        elif '*' in l:        # xrandr 1.2 and above
            w,h = l.split()[0].split('x')
        else:
            continue
        
        dim_updated = True
        if w > dim.width:
            dim = Dimension(w,h)

    if not dim_updated:
        logging.info('Cannot find xrandr or unsupported xrandr version. '
                     'The screen dimension defaults to {0}x{1}.'.format(dim.width, dim.height))
        
    return dim

class Dimension(object):
    def __init__(self, width = 640, height = 480):
        self.width = int(width)
        self.height = int(height)
        self.aspect = Fraction(self.width,self.height) if not self.height == 0 else Fraction(0)

