#!/usr/bin/env python3

import subprocess
import os
import sys
import random
import time
import re
import logging
import signal
from optparse import OptionParser

class Base(object):
    def __init__(self):
        logger = logging.getLogger(self.__class__.__name__)
        log_handler = logging.StreamHandler()
        log_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

        logger.addHandler(log_handler)

        logger.debug('Initialised logger.')

        self.logger = logger

    def __repr__(self):
        return '%s: %r' % (self.__class__.__name__, self.__dict__)

def command_line_parser():
    usage = 'usage: %prog [options]'
    parser = OptionParser(usage)

    parser.add_option(
        '-i',
        '--input-file',
        dest='input_file',
        metavar='INPUT_FILE',
        default='',
        help='Input media file (must be set) [default: "%default"]'
    )

    parser.add_option(
        '-o',
        '--ouput-file',
        dest='output_file',
        metavar='OUTPUT_FILE',
        default='',
        help='Output media file, if unset generated based on codec options and input file [default: "%default"]'
    )

    parser.add_option(
        '-v',
        '--verbose',
        dest='verbose',
        action="store_true",
        default=False,
        help='Turn on verbose logging [default: "%default"]'
    )

    parser.add_option(
        '-N',
        '--not-nice',
        dest='not_nice',
        action="store_true",
        default=False,
        help='Run not niced, i.e. normal scheduler priority [default: "%default"]'
    )

    parser.add_option(
        '-I',
        '--interactive',
        dest='interactive',
        action="store_true",
        default=False,
        help='Run ffmpeg in interactive mode [default: "%default"]'
    )

    parser.add_option(
        '-c',
        '--codec',
        dest='codec',
        metavar='CODEC',
        default='libx264',
        help='Video codec library to use [default: "%default"]'
    )

    parser.add_option(
        '-p',
        '--profile',
        dest='profile',
        metavar='PROFILE',
        default='High',
        help='Video codec profile to use [default: "%default"]'
    )

    parser.add_option(
        '-l',
        '--level',
        dest='level',
        metavar='LEVEL',
        default='6.2',
        help='Video codec level to set [default: "%default"]'
    )

    parser.add_option(
        '-r',
        '--preset',
        dest='preset',
        metavar='PRESET',
        default='9',
        help='Video codec preset to use [default: "%default"]'
    )

    parser.add_option(
        '-f',
        '--crf',
        dest='crf',
        metavar='CRF',
        default='17',
        help='Video codec constant rate factor (crf) to use [default: "%default"]'
    )

    parser.add_option(
        '-t',
        '--tune',
        dest='tune',
        metavar='TUNE',
        default='',
        help='Video codec preset to use [default: "%default"]'
    )

    parser.add_option(
        '-e',
        '--extra',
        dest='extra',
        metavar='EXTRA',
        default='',
        help='Extra args for ffmpeg [default: "%default"]'
    )

    parser.add_option(
        '-s',
        '--subprocess-out-file',
        dest='subprocess_out_file',
        metavar='SUBPROCESS_OUT_FILE',
        default='-',
        help='File to redirect subprocess output to [default: "-" (stdout)]'
    )

    return parser

def verbose_logging(verbose = False):
    logging.getLogger().setLevel(logging.DEBUG if verbose else logging.INFO)

class Fftranscode(Base):
    def __init__(self, niced, input_file, output_file, codec_lib, profile, level, preset, crf, tune, extra, subprocess_out, interactive):
        super(Fftranscode, self).__init__()
        self.niced = niced
        self.input_file = input_file
        self.output_file = output_file
        self.codec_lib = codec_lib
        self.profile = profile
        self.level = level
        self.preset = preset
        self.crf = crf
        self.tune = tune
        self.extra = extra
        self.subprocess_out = subprocess_out
        self.interactive = interactive
        self.num_waits = 0

        # One week of 1 seconds waits
        self.max_waits = 604800
        self.wait_interval = 1

        self.exit_code = None
        self.sp = None
        self.ffmpeg_ver = None

    def cancel_transcode(self, exit = True):
        self.logger.warning('Cancelling transcode.')

        if self.sp is not None:
            self.logger.warning('Subprocess still executing, sending Kill signal.')
            self.sp.kill()
            self.sp.wait()
        if exit:
            sys.exit(2)

    def handle_subprocess(self):
        if self.sp is None:
            self.logger.error('There is no subprocess when there should be! Exiting.')
            self.cancel_transcode()

        self.sp.poll()

        if self.sp.returncode is not None:
            self.running = False
            self.logger.info('Subprocess has exited. Exit code "%d".' % self.sp.returncode)
            self.exit_code = self.sp.returncode

    def gen_output_file_name(self):
        file_name = "%s - ffmpeg:%s_c:%s_p:%s_l:%s_r:%s_f:%s" % (self.input_file[:-4], self.ffmpeg_ver, self.codec_lib, self.profile, self.level, self.preset, self.crf)

        if self.tune != '':
            file_name  += "_t:%s" % self.tune

        file_name += ".mkv"

        self.logger.info("Generated output file name: %s" % file_name)

        return file_name

    def gen_transcode_args(self):
        args = [ 'ffmpeg',
                '-hide_banner',
                '-n',
                '-i',
                self.input_file,
                '-map',
                '0',
                '-codec:a',
                'copy',
                '-codec:s',
                'copy',
                '-codec:v',
                self.codec_lib,
                '-profile:v',
                self.profile,
                '-level',
                self.level,
                '-preset',
                self.preset,
                '-crf',
                self.crf
        ]

        if self.niced == True:
            args.insert(0, 'nice')

        if self.tune != '':
            args.append('-tune')
            args.append(self.tune)

        if self.extra != '':
            for tok in self.extra.split():
                args.append(tok)

        if self.interactive == False:
            args.append('-nostdin')

        if self.output_file == '':
            self.output_file = self.gen_output_file_name()

        args.append(self.output_file)

        return args

    def get_ffencode_version(self):
        ffencode_pipe = subprocess.Popen(['ffmpeg', '-version'], stdout=subprocess.PIPE)
        buff = ffencode_pipe.communicate()

        m = re.compile(b'^ffmpeg version (.+) Copyright').match(buff[0])

        if m:
            # This gets ingested as a bytes array and needs to be encoded to UTF-8
            # to avoid it being printed as b'...'
            ver = m.groups()[0].decode('UTF-8')
            self.logger.info("ffmpeg version: %s" % ver)

        self.ffmpeg_ver = ver

    def transcode(self):
        if self.subprocess_out != '-':
            outfile = open(self.subprocess_out, 'w')
            self.logger.info('Opened file "%s" for subprocess output.' % self.subprocess_out)
        else:
            self.logger.info('Using stdout for subporcess output.')
            outfile = None

        # stderr is set to the same FD as stdout so the error output has context.
        # Not all errors are informative, may need to dig through contextual output
        # for approximate error cause.

        self.get_ffencode_version()
        args = self.gen_transcode_args()
        self.logger.info('Starting Popen with args: %s' % args)
        self.sp = subprocess.Popen(args, stderr=subprocess.STDOUT, stdout = outfile)
        self.running = True

        while self.running == True and self.num_waits < self.max_waits:
            self.num_waits += 1
            time.sleep(self.wait_interval)

            self.handle_subprocess()

        if self.num_waits == self.max_waits:
            raise Exception("Transcode took longer tham max %s seconds." % (self.max_waits * self.wait_interval))

        return self.exit_code

def signal_handler(signum, stack_frame):
    raise Exception("Caught signal %d with stack frame: %s" % (signum, stack_frame))

if __name__ == '__main__':
    parser = command_line_parser()

    (options, args) = parser.parse_args()

    verbose_logging(options.verbose)

    if len(options.input_file) > 0:
        try:
            fftranscode = Fftranscode(not options.not_nice, options.input_file, options.output_file, options.codec, options.profile, options.level, options.preset, options.crf, options.tune, options.extra, options.subprocess_out_file, options.interactive)
            signal.signal(signal.SIGINT, signal_handler)
            print(fftranscode)
            exit_code = fftranscode.transcode()
        except Exception as e:
            print(e)
            fftranscode.logger.error('Exception: %s', type(e))
            fftranscode.cancel_transcode(exit = False)
            raise
    else:
        print('ERROR: input file must be set.')
        sys.exit(1)

    sys.exit(exit_code)
