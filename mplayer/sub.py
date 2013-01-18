#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-01-18 23:48:46 by subi>

from __future__ import unicode_literals

from mplayer.charset import guess_locale_and_convert

import os,hashlib,logging,time,io

# interface
class RemoteSubtitleHandler(object):
    def fetch_and_save(self, save_dir=None):
        self.__subs = fetch_shooter(self.__path,self.__shash,self.__dry_run)
        if self.__subs:
            force_utf8_and_filter_duplicates(self.__subs)
            save_to_disk(self.__subs, self.__path, save_dir)

        return [s['path'] for s in self.__subs]
    
    def __init__(self, media_path, media_shash, dry_run):
        self.__path = media_path
        self.__shash = media_shash
        self.__dry_run = dry_run
        self.__subs = []

# implementation
def save_to_disk(subtitles, filepath, save_dir=None):
    prefix,_ = os.path.splitext(filepath)
    if save_dir:
        prefix = os.path.join(save_dir, os.path.basename(prefix))

    # save subtitles
    for s in subtitles:
        suffix = '.' + s['lang'] if not s['lang'] == 'und' else ''
                
        path = prefix + suffix + '.' + s['extension']
        if os.path.exists(path):
            path = prefix + suffix + '1.' + s['extension']
        with open(path,'wb') as f:
            f.write(s['content'])
            logging.info('Saved the subtitle as {0}'.format(path))
            s['path'] = path

def force_utf8_and_filter_duplicates(subtitles):
    logging.debug('Trying to filter duplicated subtitles...')

    for s in subtitles:
        _,s['lang'],s['content'] = guess_locale_and_convert(s['content'])
            
    dup_tag = [False]*len(subtitles)
    for i in range(len(subtitles)):
        if dup_tag[i]:
            continue
        for j in range(i+1, len(subtitles)):
            sa = subtitles[i]
            sb = subtitles[j]
            if sa['extension'] != sb['extension'] or sa['lang'] != sb['lang']:
                continue
            import difflib
            similarity = difflib.SequenceMatcher(None, sa['content'], sb['content']).real_quick_ratio()
            logging.debug('Similarity is {0}.'.format(similarity))
            if similarity > 0.9:
                dup_tag[j] = True
    # TODO: reserve longer subtitles 
    subtitles = [subtitles[i] for i in range(len(subtitles)) if not dup_tag[i]]
    logging.debug('{0} subtitle(s) reserved after duplicates filtering.'.format(len(subtitles)))

def parse_shooter_package(fileobj):
    '''Parse shooter returned package of subtitles.
    Return subtitles encoded in UTF-8.
    '''
    subtitles = []
    f = fileobj

    # read contents
    import struct
    c = f.read(1)
    package_count = struct.unpack(b'!b', c)[0]

    for i in range(package_count):
        # NOTE: '_' is the length of following byte-stream
        c = f.read(8)
        _,desc_length = struct.unpack(b'!II', c)
        description = f.read(desc_length).decode('utf_8')
        sub_delay = description.partition('=')[2] / 1000.0 if description and 'delay' in description else 0
        if description:
            logging.debug('Subtitle description: {0}'.format(description))

        c = f.read(5)
        _,file_count = struct.unpack(b'!IB', c)
            
        for j in range(file_count):
            c = f.read(8)
            _,ext_len = struct.unpack(b'!II', c)
            ext = f.read(ext_len)

            c = f.read(4)
            file_len = struct.unpack(b'!I', c)[0]
            sub = f.read(file_len)
            if sub.startswith(b'\x1f\x8b'):
                import gzip
                sub = gzip.GzipFile(fileobj=io.BytesIO(sub)).read()

            subtitles.append({'extension': ext,
                              'delay': sub_delay,
                              'content': sub})

    logging.debug('{0} subtitle(s) fetched.'.format(len(subtitles)))
    return subtitles

def fetch_shooter(filepath,filehash,dry_run=False):
    import httplib
    schemas = ['http', 'https'] if hasattr(httplib, 'HTTPS') else ['http']
    servers = ['www', 'splayer', 'svplayer'] + ['splayer'+str(i) for i in range(1,13)]
    splayer_rev = 2437 # as of 2012-07-02
    tries = [2, 10, 30, 60, 120]

    # generate data for submission
    # shooter.cn uses UTF-8.
    head,tail = os.path.split(filepath)
    pathinfo = '\\'.join(['D:', os.path.basename(head), tail])
    v_fingerpint = b'SP,aerSP,aer {0} &e(\xd7\x02 {1} {2}'.format(splayer_rev, pathinfo.encode('utf_8'), filehash.encode('utf_8'))
    vhash = hashlib.md5(v_fingerpint).hexdigest()
    import random
    boundary = '-'*28 + '{0:x}'.format(random.getrandbits(48))

    header = [('User-Agent',   'SPlayer Build {0}'.format(splayer_rev)),
              ('Content-Type', 'multipart/form-data; boundary={0}'.format(boundary))
              ]
    items = [('filehash', filehash), ('pathinfo', pathinfo), ('vhash', vhash)]
    data = ''.join(['--{0}\n'
                    'Content-Disposition: form-data; name="{1}"\n\n'
                    '{2}\n'.format(boundary, *d) for d in items]
                   + ['--' + boundary + '--'])

    if dry_run:
        print 'DRY-RUN: Were trying to fetch subtitles for {0}.'.format(filepath)
        return None
        
#        app.send('osd_show_text "正在查询字幕..." 5000')
#            app.send('osd_show_text "查询字幕失败." 3000')

    # fetch
    import urllib2
    for i, t in enumerate(tries):
        try:
            logging.debug('Wait for {0}s to reconnect (Try {1} of {2})...'.format(t,i+1,len(tries)+1))
            time.sleep(t)

            url = '{0}://{1}.shooter.cn/api/subapi.php'.format(random.choice(schemas), random.choice(servers))

            # shooter.cn uses UTF-8.
            req = urllib2.Request(url.encode('utf_8'))
            for h in header:
                req.add_header(h[0].encode('utf_8'), h[1].encode('utf_8'))
            req.add_data(data.encode('utf_8'))

            logging.debug('Connecting server {0} with the submission:\n'
                          '\n{1}\n'
                          '{2}\n'.format(url,
                                         '\n'.join(['{0}:{1}'.format(*h) for h in header]),
                                         data))

            # todo: with context manager
            response = urllib2.urlopen(req)
            fetched_subtitles = parse_shooter_package(response)
            response.close()

            if fetched_subtitles:
                break
        except urllib2.URLError, e:
            logging.debug(e)
    return fetched_subtitles

