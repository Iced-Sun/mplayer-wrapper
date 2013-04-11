#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-12 01:13:10 by subi>

# interface
def apply_geometry_fix(w,h,DAR_advice,DAR_force=None):
    '''Adjust the movie DAR (display aspect ratio) automatically by advice (usually
    come from video info) or force (usually come from command line) and expand the
    movie.

    Return generated arguments for mplayer.
    '''
    if DAR_force:
        DAR = DAR_force
    else:
        DAR = auto_adjust_DAR(w,h,DAR_advice)
    PAR = DAR / Fraction(w,h)
    args = []

    # apply aspect fixes only if the aspect is REALLY changed.
    if abs(DAR - Fraction(w,h)) > 0.01:
        args.append('-aspect {0.numerator}:{0.denominator}'.format(DAR))

        # work-around for stretched osd/subtitle after applying -aspect
        if w >= 1280:
            h = int(w / DAR)
        else:
            w = int(h * DAR)
        args.append('-vf-pre scale={0}:{1}'.format(w,h))
    
    return DAR, PAR, args + expand_video(DAR)

# implementation
import subprocess
from fractions import Fraction

from aux import which

def check_screen_dim():
    '''Select the maximal available screen dimension.
    '''
    dim = (640,480)
    if which('randr'):
        for l in subprocess.check_output(['randr']).splitlines():
            if l.startswith('*'): # xrandr 1.1
                _,w,_,h = l.split()
            elif '*' in l:        # xrandr 1.2 and above
                w,h = l.split()[0].split('x')
            else:
                continue
            if w > dim[0]:
                dim = (w,h)
    return dim

def auto_adjust_DAR(w,h,DAR):
    '''Determine a reasonable display aspect ratio.
    
    References:
    1. http://www.mir.com/DMG/aspect.html
    2. http://en.wikipedia.org/wiki/Pixel_aspect_ratio
    3. http://lipas.uwasa.fi/~f76998/video/conversion
    '''
    def aspect_not_stupid(a):
        # aspects of 4:3, 16:9, or >16:9 are NOT stupid
        return abs(a-Fraction(4,3))<0.02 or a-Fraction(16,9)>-0.02

    # display aspect ratio
    # FIXME: luca waltz_bus stop.mp4
    if aspect_not_stupid(DAR):
        DAR = Fraction(DAR).limit_denominator(9)
    else:
        # let's guess
        if w >= 1280:
            # http://en.wikipedia.org/wiki/High-definition_television
            # HDTV is always 16:9
            DAR = Fraction(16,9)
        elif w >= 700:
            # http://en.wikipedia.org/wiki/Enhanced-definition_television
            # EDTV can be 4:3 or 16:9, blame the video ripper if we
            # took the wrong guess
            DAR = Fraction(16,9)
        else:
            # http://en.wikipedia.org/wiki/Standard-definition_television
            # SDTV can be 4:3 or 16:9, blame the video ripper if we
            # took the wrong guess
            DAR = Fraction(4,3)
    return DAR

def expand_video(source_aspect, target_aspect=None):
    '''This function does 3 things:
    1. Video Expansion:
       Attach two black bands to the top and bottom of a video so that MPlayer
       can put OSD/texts in the bands. Be ware that the video expansion will
       change the video size (normally height), e.g. a 1280x720 video will be
       1280x960 after an expansion towards 4:3.

       Doing expansion is trivial because there is a '-vf expand' MPlayer
       option. '-vf expand' can change the size of a video to fit a specific
       aspect by attaching black bands (not scaling!). The black bands may be
       attached vertically when changing towards a smaller aspect (e.g. 16:9 to
       4:3 conversion), or horizontal vise versa (e.g. 4:3 to 16:9 conversion).

       The '-ass-use-margins' was once used instead because of the
       incompatibility (subtitle overlapping issue) between '-vf expand' and
       the '-ass' subtitle renderer. This method can only attach black bands
       vertically ('-ass-top-margin' and '-ass-bottom-margin').
           
    2. Font-size Normalization:
       Make the subtitle font be the same size when displayed full-screen in
       the same screen for any video.
           
    3. Expansion Compactness:
       Place the subtitle as close to the picture as possible (instead of the
       very top/bottom of the screen, which is visual distracting).

    The problems of the Font-size Normalization and Expansion Compactness bring
    the main complexities. To understand what has been done here, we first
    describe how the font size is determined in MPlayer.

    There are two OSD/subtitle renderers in MPlayer: one depends FreeType and
    the other libass. OSD is always rendered through FreeType; text subtitles
    (not vobsub) are rendered through libass if '-ass' is specified, or
    FreeType otherwise.

    As for mplayer2, the FreeType renderer has been dropped completely since
    Aug. 2012; libass is used for all the OSD/subtitle rendering.
    (http://git.mplayer2.org/mplayer2/commit/?id=083e6e3e1a9bf9ee470f59cfd1c775c8724c5ed9)
    The -noass option is indeed applying a plain ASS style that mimics the
    FreeType renderer.

    From here on, (X,Y) denotes the display size of a video. Remember that its
    aspect ratio is subject to being changed by '-vf expand'.
        
    Here are the details:
    1. The FreeType Renderer:
                      font_size = movie_size * font_scale_factor / 100
       Simple and clear.

       The movie_size is determined by the option '-subfont-autoscale':
       a. subfont-autoscale = 0 (no autoscale):
                      movie_size = 100
       b. subfont-autoscale = 1 (proportional to video height):
                      movie_size = Y
       c. subfont-autoscale = 2 (proportional to video width):
                      movie_size = X
       d. subfont-autoscale = 3 (proportional to video diagonal):
                      movie_size = sqrt(X^2+Y^2)

       The font_scale_factor is assigned by the option '-subfont-text-scale'
       and '-subfont-osd-scale', which defaults to 3.5 and 4.0, respectively,
       and applies to subtitles and OSD, respectively.
           
    2. The libass Renderer:
       MPlayer provides some ASS styles for libass; font-scale involved styles
       are:
       i).  PlayResX,PlayResY
            They are the source frame (i.e. the reference frame for ASS style
            with an absolute value, e.g. FontSize and Margins).

            PlayResY defaults to 288 and. PlayResX defaults to PlayResY/1.3333,
            which is 384.
       ii). ScaleX,ScaleY
            These font scales default to 1.
       iii).FontSize
            As pointed above, the FontSize is the size in the reference frame
            (PlayResX,PlayResY).
                      FontSize = PlayResY * font_scale_factor / 100

            The font_scale_factor is again assigned by the option
            '-subfont-text-scale' and '-subfont-osd-scale', which applies to
            subtitles and OSD, respectively. (Be ware that OSD is rendered by
            libass in mplayer2.)
                      
            The option '-subfont-autoscale' will affect the FontSize as
            well. Although this is in fact meaningless because the font size is
            always proportional to video height only in libass renderer, a
            correction that corresponds to about 4:3 aspect ratio video is
            applied to get a size somewhat closer to what non-libass rendering.
            a. subfont-autoscale = 0 (no autoscale):
                      ignored
            b. subfont-autoscale = 1 (proportional to video height):
                      ignored
            c. subfont-autoscale = 2 (proportional to video width):
                      FontSize *= 1.3
            d. subfont-autoscale = 3 (proportional to video diagonal):
                      FontSize *= 1.4 (mplayer)
                      FontSize *= 1.7 (mplayer2)
       iv). font_size_coeff
            This is not an ASS style but an extra parameter for libass. The
            value is specified by the '-ass-font-scale' MPlayer option.

       libass does the real work as follows:
       i). determine the scale factor for (PlayResX,PlayResY)->(X,Y)
                      font_scale = font_size_coeff * Y/PlayResY
       ii).apply the font scale:
                      font_x = FontSize * font_scale * ScaleX
                      font_y = FontSize * font_scale * ScaleY

       Combining all the procedure and supposing that the ASS styles take
       default values, the font size displayed in frame (X,Y) is:
               font_size = font_size_coeff * font_scale_factor * Y/100  (*)

    Now let's try the font size normalization. What we need is the Y_screen
    proportionality to the font size. (Of course you can use X or diagonal
    instead of Y).
    1. If we don't care the expansion compactness, the video will be expanded
       to the screen aspect, hence Y = Y_screen. There is nothing to be done
       for libass renderer, as the font size is proportional to Y (see *).

       For the FreeType renderer, as long as subfont-autoscale is enabled, the
       font size is always proportional to Y because of the fixed aspect.

    2. If we want to make the expansion compact, the display aspect of the
       expanded video will be typically larger than the screen aspect. Let's do
       some calculations:
       a. display_aspect > screen_aspect
                    font_size ∝ Y = Y_screen / display_aspect:screen_aspect
       b. display_aspect = screen_aspect
                    font_size ∝ Y = Y_screen
       c. display_aspect < screen_aspect
          Won't happen because we do horizontal expansion if display_aspect <
          screen_aspect so the result is always display_aspect = screen_aspect.

    Let font_scale_factor *= display_aspect:screen_aspect resolve all the
    problem. For FreeType renderer, we will want to set subfont-autoscale=1 or
    else an additional correction has to be applied.
    '''
    if not target_aspect:
        target_aspect = Fraction(*check_screen_dim())
    
    # default scales
    scale_base_factor = 1.5
    subfont_text_scale = 3.5 * scale_base_factor
    subfont_osd_scale = 4 * scale_base_factor

    # expansion compactness:
    # a margin take 1.8 lines of 18-pixels font in screen of height 288
    margin_scale = 18.0/288.0 * 1.8 * scale_base_factor
    expected_aspect = source_aspect / (1+2*margin_scale)
    if expected_aspect > target_aspect:
        display_aspect = expected_aspect
        subfont_text_scale *= expected_aspect/target_aspect
    else:
        display_aspect = target_aspect

    # generate MPlayer args
    args = ['-subfont-autoscale 1', # proportional to height
            '-vf-add expand=::::1:{0}'.format(display_aspect),
            '-subfont-text-scale {0}'.format(subfont_text_scale),
            '-subfont-osd-scale {0}'.format(subfont_osd_scale)]

    return args

if __name__ == '__main__':
    print(apply_geometry_fix(1152, 768, Fraction(16,9)))
