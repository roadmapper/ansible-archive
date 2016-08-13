#!/usr/bin/python

# -*- coding: utf-8 -*-

# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
# (c) 2013, Dylan Martin <dmartin@seattlecentral.edu>
# (c) 2015, Toshio Kuratomi <tkuratomi@ansible.com>
# (c) 2016, Dag Wieers <dag@wieers.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: archive
short_description: Packs an archive.
extends_documentation_fragment: files
description:
     - The M(archive) module unpacks an archive. By default, it will copy the source file from the local system to the target before unpacking - set remote_src=yes to unpack an archive which already exists on the target..
options:
  src:
    description:
      - If remote_src=no (default), local path to archive file to copy to the target server; can be absolute or relative. If remote_src=yes, path on the target server to existing archive file to unpack.
      - If remote_src=yes and src contains ://, the remote machine will download the file from the url first. (version_added 2.0)
    required: true
    default: null
  dest:
    description:
      - Remote absolute path where the archive should be unpacked
    required: true
    default: null
  copy:
    description:
      - "If true, the file is copied from local 'master' to the target machine, otherwise, the plugin will look for src archive at the target machine."
      - "This option has been deprecated in favor of C(remote_src)"
      - "This option is mutually exclusive with C(remote_src)."
    required: false
    choices: [ "yes", "no" ]
    default: "yes"
  creates:
    description:
      - a filename, when it already exists, this step will B(not) be run.
    required: no
    default: null
    version_added: "1.6"
  list_files:
    description:
      - If set to True, return the list of files that are contained in the tarball.
    required: false
    choices: [ "yes", "no" ]
    default: "no"
    version_added: "2.0"
  exclude:
    description:
      - List the directory and file entries that you would like to exclude from the unarchive action.
    required: false
    default: []
    version_added: "2.1"
  keep_newer:
    description:
      - Do not replace existing files that are newer than files from the archive.
    required: false
    default: no
    version_added: "2.1"
  extra_opts:
    description:
      - Specify additional options by passing in an array.
    default:
    required: false
    version_added: "2.1"
  remote_src:
    description:
      - "Set to C(yes) to indicate the archived file is already on the remote system and not local to the Ansible controller."
      - "This option is mutually exclusive with C(copy)."
    required: false
    default: "no"
    choices: ["yes", "no"]
    version_added: "2.2"
  validate_certs:
    description:
      - This only applies if using a https url as the source of the file.
      - This should only set to C(no) used on personally controlled sites using self-signed cer
      - Prior to 2.2 the code worked as if this was set to C(yes).
    required: false
    default: "yes"
    choices: ["yes", "no"]
    version_added: "2.2"
author: "Dag Wieers (@dagwieers)"
todo:
    - re-implement tar support using native tarfile module
    - re-implement zip support using native zipfile module
notes:
    - requires C(gtar)/C(unzip) command on target host
    - can handle I(gzip), I(bzip2) and I(xz) compressed as well as uncompressed tar files
    - detects type of archive automatically
    - uses gtar's C(--diff arg) to calculate if changed or not. If this C(arg) is not
      supported, it will always unpack the archive
    - existing files/directories in the destination which are not in the archive
      are not touched.  This is the same behavior as a normal archive extraction
    - existing files/directories in the destination which are not in the archive
      are ignored for purposes of deciding if the archive should be unpacked or not
'''

EXAMPLES = '''
# Example from Ansible Playbooks
- archive: src=/var/lib/foo dest=foo.tgz
'''

import re
import os
import stat
import pwd
import grp
import datetime
import time
import binascii
import codecs
from zipfile import ZipFile, BadZipfile

# String from tar that shows the tar contents are different from the
# filesystem
OWNER_DIFF_RE = re.compile(r': Uid differs$')
GROUP_DIFF_RE = re.compile(r': Gid differs$')
MODE_DIFF_RE = re.compile(r': Mode differs$')
MOD_TIME_DIFF_RE = re.compile(r': Mod time differs$')
#NEWER_DIFF_RE = re.compile(r' is newer or same age.$')
MISSING_FILE_RE = re.compile(r': Warning: Cannot stat: No such file or directory$')
ZIP_FILE_MODE_RE = re.compile(r'([r-][w-][stx-]){3}')

class ArchiveError(Exception):
    pass

# class to handle gzipped tar files
class TgzArchive(object):

    def __init__(self, src, dest, file_args, module):
        self.src = src
        self.dest = dest
        self.file_args = file_args
        self.opts = module.params['extra_opts']
        self.module = module
        self.excludes = [ path.rstrip('/') for path in self.module.params['exclude']]
        # Prefer gtar (GNU tar) as it supports the compression options -zjJ
        self.cmd_path = self.module.get_bin_path('gtar', None)
        if not self.cmd_path:
            # Fallback to tar
            self.cmd_path = self.module.get_bin_path('tar')
        self.zipflag = 'z'
        self.compress_mode = 'gz'
        self._files_in_archive = []

#     @property
#     def files_in_archive(self, force_refresh=False):
#         if self._files_in_archive and not force_refresh:
#             return self._files_in_archive

#         cmd = '%s -t%s' % (self.cmd_path, self.zipflag)
#         if self.opts:
#             cmd += ' ' + ' '.join(self.opts)
#         if self.excludes:
#             cmd += ' --exclude="' + '" --exclude="'.join(self.excludes) + '"'
#         cmd += ' -f "%s"' % self.src
#         rc, out, err = self.module.run_command(cmd)
#         if rc != 0:
#             raise ArchiveError('Unable to list files in the archive')

#         for filename in out.splitlines():
#             # Compensate for locale-related problems in gtar output (octal unicode representation) #11348
# #            filename = filename.decode('string_escape')
#             filename = codecs.escape_decode(filename)[0]
#             if filename and filename not in self.excludes:
#                 self._files_in_archive.append(filename)
#         return self._files_in_archive

    def is_archived(self):
        cmd = '%s -c%s' % (self.cmd_path, self.zipflag)
        if self.opts:
            cmd += ' ' + ' '.join(self.opts)
        if self.file_args['owner']:
            cmd += ' --owner="%s"' % self.file_args['owner']
        if self.file_args['group']:
            cmd += ' --group="%s"' % self.file_args['group']
        if self.file_args['mode']:
            cmd += ' --mode="%s"' % self.file_args['mode']
        if self.excludes:
            cmd += ' --exclude="' + '" --exclude="'.join(self.excludes) + '"'
        cmd += ' -f "%s"' % self.src
        rc, out, err = self.module.run_command(cmd)

        # Check whether the differences are in something that we're
        # setting anyway

        # What is different
        archived = True
        old_out = out
        out = ''
        run_uid = os.getuid()
        # When archiving as a user, or when owner/group/mode is supplied --diff is insufficient
        # Only way to be sure is to check request with what is on disk (as we do for zip)
        # Leave this up to set_fs_attributes_if_different() instead of inducing a (false) change
        for line in old_out.splitlines() + err.splitlines():
            if run_uid == 0 and not self.file_args['owner'] and OWNER_DIFF_RE.search(line):
                out += line + '\n'
            if run_uid == 0 and not self.file_args['group'] and GROUP_DIFF_RE.search(line):
                out += line + '\n'
            if not self.file_args['mode'] and MODE_DIFF_RE.search(line):
                out += line + '\n'
            if MOD_TIME_DIFF_RE.search(line):
                out += line + '\n'
            if MISSING_FILE_RE.search(line):
                out += line + '\n'
        if out:
            archived = False
        return dict(archived=archived, rc=rc, out=out, err=err, cmd=cmd)

    def archive(self):
        cmd = '%s -c%s' % (self.cmd_path, self.zipflag)
        if self.opts:
            cmd += ' ' + ' '.join(self.opts)
        if self.file_args['owner']:
            cmd += ' --owner="%s"' % self.file_args['owner']
        if self.file_args['group']:
            cmd += ' --group="%s"' % self.file_args['group']
        if self.file_args['mode']:
            cmd += ' --mode="%s"' % self.file_args['mode']
        if self.excludes:
            cmd += ' --exclude="' + '" --exclude="'.join(self.excludes) + '"'
        cmd += ' -f "%s" "%s"' % (self.dest, self.src)
        rc, out, err = self.module.run_command(cmd, cwd=self.dest)
        return dict(cmd=cmd, rc=rc, out=out, err=err)

    def can_handle_archive(self):
        if not self.cmd_path:
            return False

        if 'z' in self.options:
            return True
        
        # Errors and no files in archive assume that we weren't able to
        # properly unarchive it
        return False


# try handlers in order and return the one that works or bail if none work
def pick_handler(src, dest, file_args, options, module):
    handlers = [TgzArchive]#, ZipArchive, TarArchive, TarBzipArchive, TarXzArchive]
    for handler in handlers:
        obj = handler(src, dest, file_args, module)
        if obj.can_handle_archive():
            return obj
    module.fail_json(msg='Failed to find handler for "%s". Make sure the required command to extract the file is installed.' % src)


def main():
    module = AnsibleModule(
        # not checking because of daisy chain to file module
        argument_spec = dict(
            src                   = dict(required=True, type='path'),
            dest                  = dict(required=True, type='path'),
            options               = dict(required=True, type='str'),
            change_directory_path = dict(required=False, type='path'),
            exclude               = dict(required=False, default=[], type='list'),
            extra_opts            = dict(required=False, default=[], type='list')
        ),
        add_file_common_args = True
        #mutually_exclusive   = [("copy", "remote_src"),]
        # check-mode only works for zip files
        #supports_check_mode = True,
    )

    # We screenscrape a huge amount of commands so use C locale anytime we do
    module.run_command_environ_update = dict(LANG='C', LC_ALL='C', LC_MESSAGES='C', LC_CTYPE='C')

    src                   = os.path.expanduser(module.params['src'])
    dest                  = os.path.expanduser(module.params['dest'])
    options               = module.params['options']
    change_directory_path = os.path.expanduser(module.params['change_directory_path'])
    file_args             = module.load_file_common_arguments(module.params)

    # does the source exist?
    if not os.path.isdir(src):
        module.fail_json(msg="Source '%s' does not exist" % src)
    if not os.access(src, os.R_OK):
        module.fail_json(msg="Source '%s' not readable" % src)

    # if the change directory path is specified, does it exist and is it accessible?
    if change_directory_path:
        if not os.path.isdir(change_directory_path):
            module.fail_json(msg="Change directory path '%s' does not exist" % change_directory_path)
        if not os.access(change_directory_path, os.R_OK):
            module.fail_json(msg="Change directory path '%s' not readable" % change_directory_path)
    

    handler = pick_handler(src, dest, file_args, options, module)
    res_args = dict(handler=handler.__class__.__name__, dest=dest, src=src)

    # do we need to do pack?
    check_results = handler.is_archived()

    # do the unpack
    try:
        res_args['extract_results'] = handler.archive()
        if res_args['extract_results']['rc'] != 0:
            module.fail_json(msg="failed to pack %s to %s" % (src, dest), **res_args)
    except IOError:
        module.fail_json(msg="failed to pack %s to %s" % (src, dest), **res_args)
    else:
        res_args['changed'] = True

    module.exit_json(**res_args)


# import module snippets
from ansible.module_utils.basic import *
if __name__ == '__main__':
    main()
