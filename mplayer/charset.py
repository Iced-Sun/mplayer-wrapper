#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-10 00:46:24 by subi>

from __future__ import unicode_literals

# interface
def guess_locale_and_convert(stream):
    enc,lang = guess_locale(stream)

    if isinstance(lang,int):
        stream = stream[lang:]
        lang = 'und'
        
    if not enc in ['utf_8', 'ascii']:
        stream = stream.decode(enc,'ignore').encode('utf_8')

    return enc,lang,stream

# implementation
import re

class Charset(object):
    # http://unicode.org/faq/utf_bom.html#BOM
    bom = ((b'\x00\x00\xFE\xFF', 'utf_32_be'), (b'\xFF\xFE\x00\x00', 'utf_32_le'),
           (b'\xFE\xFF',         'utf_16_be'), (b'\xFF\xFE',         'utf_16_le'),
           (b'\xEF\xBB\xBF',     'utf_8'), )

    codec = {}

    # http://en.wikipedia.org/wiki/Ascii
    codec['ascii'] = (b'[\x09\x0A\x0D\x20-\x7E]',)

    # http://en.wikipedia.org/wiki/GBK
    codec['gbk'] = (b'[\xA1-\xA9][\xA1-\xFE]',              # Level GBK/1
                    b'[\xB0-\xF7][\xA1-\xFE]',              # Level GBK/2
                    b'[\x81-\xA0][\x40-\x7E\x80-\xFE]',     # Level GBK/3
                    b'[\xAA-\xFE][\x40-\x7E\x80-\xA0]',     # Level GBK/4
                    b'[\xA8-\xA9][\x40-\x7E\x80-\xA0]',     # Level GBK/5
                    b'[\xAA-\xAF][\xA1-\xFE]',              # user-defined
                    b'[\xF8-\xFE][\xA1-\xFE]',              # user-defined
                    b'[\xA1-\xA7][\x40-\x7E\x80-\xA0]',     # user-defined
                    )
    codec['gb2312'] = codec['gbk'][0:2]

    # http://www.cns11643.gov.tw/AIDB/encodings.do#encode4
    codec['big5'] = (b'[\xA4-\xC5][\x40-\x7E\xA1-\xFE]|\xC6[\x40-\x7E]',          # 常用字
                     b'\xC6[\xA1-\xFE]|[\xC7\xC8][\x40-\x7E\xA1-\xFE]',           # 常用字保留範圍/罕用符號區
                     b'[\xC9-\xF8][\x40-\x7E\xA1-\xFE]|\xF9[\x40-\x7E\xA1-\xD5]', # 次常用字
                     b'\xF9[\xD6-\xFE]',                                          # 次常用字保留範圍
                     b'[\xA1-\xA2][\x40-\x7E\xA1-\xFE]|\xA3[\x40-\x7E\xA1-\xBF]', # 符號區標準字
                     b'\xA3[\xC0-\xE0]',                                          # 符號區控制碼
                     b'\xA3[\xE1-\xFE]',                                          # 符號區控制碼保留範圍
                     b'[\xFA-\xFE][\x40-\x7E\xA1-\xFE]',                          # 使用者造字第一段
                     b'[\x8E-\xA0][\x40-\x7E\xA1-\xFE]',                          # 使用者造字第二段
                     b'[\x81-\x8D][\x40-\x7E\xA1-\xFE]',                          # 使用者造字第三段
                     )

    # http://www.w3.org/International/questions/qa-forms-utf-8
    codec['utf_8'] = (b'[\xC2-\xDF][\x80-\xBF]',            # non-overlong 2-byte
                      b'\xE0[\xA0-\xBF][\x80-\xBF]',        # excluding overlongs
                      b'[\xE1-\xEC\xEE\xEF][\x80-\xBF]{2}', # straight 3-byte
                      b'\xED[\x80-\x9F][\x80-\xBF]',        # excluding surrogates
                      b'\xF0[\x90-\xBF][\x80-\xBF]{2}',     # planes 1-3
                      b'[\xF1-\xF3][\x80-\xBF]{3}',         # planes 4-15
                      b'\xF4[\x80-\x8F][\x80-\xBF]{2}',     # plane 16
                      )

    @staticmethod
    def re(enc, with_ascii=True):
        if with_ascii and not enc == 'ascii':
            return b'|'.join(Charset.codec['ascii'] + Charset.codec[enc])
        else:
            return b'|'.join(Charset.codec[enc])

def filter_in(stream, regex):
    # find matches and join them
    return b''.join(re.findall(regex, stream))

def filter_out(stream, regex):
    # kick out matches and join the remains
    return re.sub(regex, b'', stream)

def interprete_stream(stream, enc):
    '''ASCII bytes (\x00-\x7F) can be standalone or be the low byte of the
    pattern. We count them separately.
      
    @pattern: the list of code points
    Return: (#ASCII, #ENC, #OTHER)
    '''
    interpretable = filter_in(stream, Charset.re(enc))
    standalone_ascii = filter_out(interpretable, Charset.re(enc,False))
    
    return len(standalone_ascii), len(interpretable)-len(standalone_ascii), len(stream)-len(interpretable)

def guess_locale(stream, naive=True):
    # detect if there is BOM
    for sig,enc in Charset.bom:
        if stream.startswith(sig):
            return enc,len(sig)
        
    # prepare the sample
    # filter out ASCII as much as possible by the heuristic that a \x00-\x7F
    # byte that following \x80-\xFF is not ASCII.
    pattern = b'(?<![\x80-\xFE])' + Charset.re('ascii')
    sample = filter_out(stream, pattern)
    
    if len(sample)>2048:
        sample = sample[0:2048]

    # true when having less than 0.5% (~10) bytes cannot be interpreted
    threshold = int(len(sample) * .005)
    if interprete_stream(sample, 'utf_8')[2] <= threshold:
        return 'utf_8','und'
    elif naive:
        # In the particular context of subtitles, traditional Chinese is more
        # likely encoded in BIG5 rather than GBK.
        #
        # The priority is GB2312>BIG5>GBK when the bytes can interpreted by at
        # least two of them. If this is not you want, please set naive=False.
        for enc,lang in [('gb2312','chs'), ('big5','cht'), ('gbk','cht')]:
            if interprete_stream(sample, enc)[2] <= threshold:
               return enc,lang 
    else:
        # GBK and BIG5 share most code points and hence it's almost impossible
        # to take a right guess by only counting non-interpretable bytes.
        #
        # A clever statistic approach can be found at:
        # http://www.ibiblio.org/pub/packages/ccic/software/data/chrecog.gb.html
        l = len(re.findall('[\xA1-\xFE][\x40-\x7E]',sample))
        h = len(re.findall('[\xA1-\xFE][\xA1-\xFE]',sample))
        if l == 0:
            return 'gb2312','chs'
        elif float(l)/float(h) < 0.25:
            return 'gbk','chi'
        else:
            return 'big5','cht'
    return 'ascii','eng'

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 1:
        for path in sys.argv[1:]:
            with open(path,'rb') as f:
                print(path,guess_locale(f.read()))
