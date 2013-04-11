#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2013 Bing Sun <subi.the.dream.walker@gmail.com>
# Time-stamp: <2013-04-11 18:04:15 by subi>

from __future__ import unicode_literals

from global_setting import config
from aux import log_debug, log_info

# interface
def fetch_subtitle(media_path, media_shash, save_dir=None):
    saved_path = []
    
    subs = fetch_shooter(media_path, media_shash)
    if subs:
        force_utf8_and_filter_duplicates(subs)
        save_to_disk(subs, media_path, save_dir)
        saved_path = [s['path'] for s in subs]
        
    return saved_path
    
# implementation
from charset import guess_locale_and_convert
import os,hashlib,logging,time,io

def save_to_disk(subtitles, filepath, save_dir):
    prefix,_ = os.path.splitext(filepath)
    if save_dir:
        prefix = os.path.join(save_dir, os.path.basename(prefix))

    # save subtitles
    for s in subtitles:
        suffix = '.' + s['lang'] if not s['lang'] == 'und' else ''
        while os.path.exists(prefix+suffix+'.'+s['extension']):
            suffix = suffix + '1'

        path = prefix + suffix + '.' + s['extension']

        with open(path,'wb') as f:
            f.write(s['content'])
            log_info('Saved the subtitle as {0}'.format(path))
            s['path'] = path

def force_utf8_and_filter_duplicates(subtitles):
    log_debug('Trying to filter duplicated subtitles...')

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
            log_debug('Similarity is {0}.'.format(similarity))
            if similarity > 0.9:
                dup_tag[j] = True
    # TODO: reserve longer subtitles 
    subtitles = [subtitles[i] for i in range(len(subtitles)) if not dup_tag[i]]
    log_debug('{0} subtitle(s) reserved after duplicates filtering.'.format(len(subtitles)))

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
            log_debug('Subtitle description: {0}'.format(description))

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

    log_debug('{0} subtitle(s) fetched.'.format(len(subtitles)))
    return subtitles

def fetch_shooter(filepath,filehash):
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

    if config.DRY_RUN:
        log_info('fetch_shooter() ---> Dry-running:\n Fetching subtitles for {0}.'.format(filepath))
        return None
        
#        app.send('osd_show_text "正在查询字幕..." 5000')
#            app.send('osd_show_text "查询字幕失败." 3000')

    # fetch
    import urllib2
    for i, t in enumerate(tries):
        log_debug('Wait for {0}s to reconnect (Try {1} of {2})...'.format(t,i+1,len(tries)+1))
        time.sleep(t)

        url = '{0}://{1}.shooter.cn/api/subapi.php'.format(random.choice(schemas), random.choice(servers))

        # shooter.cn uses UTF-8.
        req = urllib2.Request(url.encode('utf_8'))
        for h in header:
            req.add_header(h[0].encode('utf_8'), h[1].encode('utf_8'))
        req.add_data(data.encode('utf_8'))

        log_debug('Connecting server {} with the submission:\n'
                      '\n'
                      '{}\n'
                      '{}\n'.format(url,
                                     '\n'.join(['{0}:{1}'.format(*h) for h in header]),
                                     data))

        # todo: with context manager
        try:
            response = urllib2.urlopen(req)
        except StandardError as e:
            log_debug(e)
        else:
            fetched_subtitles = parse_shooter_package(response)
            if fetched_subtitles:
                break
        finally:
            response.close()

    return fetched_subtitles

