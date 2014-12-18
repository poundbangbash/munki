#!/usr/bin/python
# encoding: utf-8
#
# Copyright 2014 Greg Neagle.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
profiles.py
Munki module for working with configuration profiles.
"""

import os
import re
import subprocess
import tempfile

import FoundationPlist
import munkicommon


CONFIG_PROFILE_INFO = None
def config_profile_info(ignore_cache=False):
    '''Returns a dictionary representing the output of `profiles -C -o`'''
    global CONFIG_PROFILE_INFO
    if not ignore_cache and CONFIG_PROFILE_INFO is not None:
        return CONFIG_PROFILE_INFO
    output_plist = tempfile.mkdtemp(dir=munkicommon.tmpdir())
    cmd = ['/usr/bin/profiles', '-C', '-o', output_plist]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.communicate()
    if proc.returncode != 0:
        munkicommon.display_error(
            'Could not obtain configuration profile info: %s' % proc.stderr)
        CONFIG_PROFILE_INFO = {}
    else:
        try:
            CONFIG_PROFILE_INFO = FoundationPlist.readPlist(
                output_plist + '.plist')
        except BaseException, err:
            munkicommon.display_error(
                'Could not read configuration profile info: %s' % err)
            CONFIG_PROFILE_INFO = {}
        finally:
            try:
                os.unlink(output_plist + '.plist')
            except BaseException:
                pass
        return CONFIG_PROFILE_INFO


def profile_info_for_installed_identifier(identifier, ignore_cache=False):
    '''Returns the info dict for an installed profile identified by
    identifier, or empty dict if identifier not found.'''
    for profile in config_profile_info(
            ignore_cache=ignore_cache).get('_computerlevel', []):
        if profile['ProfileIdentifier'] == identifier:
            return profile
    return {}


def identifier_in_config_profile_info(identifier):
    '''Returns True if identifier is among the installed PayloadIdentifiers,
    False otherwise'''
    for profile in config_profile_info().get('_computerlevel', []):
        if profile['ProfileIdentifier'] == identifier:
            return True
    return False


def profile_receipt_data_path():
    '''Returns the path to our installed profile data store'''
    ManagedInstallDir = munkicommon.pref('ManagedInstallDir')
    return os.path.join(ManagedInstallDir, 'ConfigProfileData.plist')


def profile_receipt_data():
    '''Reads profile install data'''
    try:
        profile_data = FoundationPlist.readPlist(profile_receipt_data_path())
        return profile_data
    except BaseException:
        return {}


def store_profile_receipt_data(identifier, hash_value):
    '''Stores info for profile identifier.
    If hash_value is None, item is removed from the datastore.'''
    profile_data = profile_receipt_data()
    if hash_value is not None:
        profile_dict = profile_info_for_installed_identifier(identifier,
                                                             ignore_cache=True)
        install_date = profile_dict.get('ProfileInstallDate', 'UNKNOWN')
        profile_data[identifier] = {
            'FileHash': hash_value,
            'ProfileInstallDate': install_date
        }
    elif identifier in profile_data.keys():
        del profile_data[identifier]
    try:
        FoundationPlist.writePlist(profile_data, profile_receipt_data_path())
    except BaseException, err:
        munkicommon.display_error(
            'Cannot update hash for %s: %s' % (identifier, err))


def read_profile(profile_path):
    '''Reads a profile.'''
    try:
        return FoundationPlist.readPlist(profile_path)
    except FoundationPlist.NSPropertyListSerializationException:
        # possibly a signed profile
        return read_signed_profile(profile_path)
    except BaseException, err:
        munkicommon.display_error(
            'Error reading profile %s: %s' % (profile_path, err))
        return {}


def read_signed_profile(profile_path):
    '''Attempts to read a signed profile. This is a bit hacky, as we're just
    searching the data for an embedded plist. If Apple ever allows binary-style
    plists in the signed profile, this will break.'''

    # filed for future reference:
    # openssl smime -inform DER -verify -in Signed.mobileconfig
    #                           -noverify -out Unsigned.mobileconfig
    # will strip the signing from a signed profile
    # this might be a better approach
    # from: http://apple.stackexchange.com/questions/105981/
    #       how-do-i-view-or-verify-signed-mobileconfig-files-using-terminal

    try:
        fileobj = open(profile_path, mode='r')
        data = fileobj.read()
        fileobj.close()
    except (OSError, IOError), err:
        munkicommon.display_error(
            'Could not read %s: %s', pathname, err)
        return {}
    (header, xml_tag, remainder) = data.partition('<?xml')
    if xml_tag and remainder:
        (plist_content,
         plist_end_tag, trailer) = remainder.rpartition('</plist>')
        if plist_end_tag:
            plist_data = xml_tag + plist_content + plist_end_tag
            try:
                return FoundationPlist.readPlistFromString(plist_data)
            except FoundationPlist.NSPropertyListSerializationException, err:
                munkicommon.display_error(
                    'Error reading profile %s: %s' % (profile_path, err))
                return {}
    # if we get here we could not figure out what we are dealing with
    munkicommon.display_error(
        'Error reading profile %s: cannot determine format.' % profile_path)
    return {}


def record_profile_receipt(profile_path):
    '''Stores a receipt for this profile in our profile tracking plist'''
    profile_identifier = read_profile(profile_path).get('PayloadIdentifier')
    profile_hash = munkicommon.getsha256hash(profile_path)
    if profile_identifier:
        store_profile_receipt_data(profile_identifier, profile_hash)


def remove_profile_receipt(identifier):
    '''Removes the stored hash for profile with identifier'''
    store_profile_receipt_data(identifier, None)


def get_profile_receipt(profile_identifier):
    '''Returns the receipt dict for profile_identifier'''
    return profile_receipt_data().get(profile_identifier)


def install_profile(profile_path):
    '''Installs a profile. Returns True on success, False otherwise'''
    cmd = ['/usr/bin/profiles', '-IF', profile_path]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.communicate()
    if proc.returncode != 0:
        munkicommon.display_error(
            'Profile %s installation failed: %s'
            % (os.path.basename(profile_path), proc.stderr))
        return False
    record_profile_receipt(profile_path)
    return True


def remove_profile(identifier):
    '''Removes a profile with the given identifier. Returns True on success,
    False otherwise'''
    cmd = ['/usr/bin/profiles', '-Rp', identifier]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.communicate()
    if proc.returncode != 0:
        munkicommon.display_error(
            'Profile %s removal failed: %s' % (identifier, proc.stderr))
        return False
    remove_profile_receipt(identifier)
    return True


def profile_needs_to_be_installed(identifier, hash_value):
    '''If any of these conditions is True, we should install the profile:
    1) identifier is not in the output of `profiles -C`
    2) We don't have a receipt for this profile identifier
    3) receipt's hash_value for identifier does not match ours
    4) ProfileInstallDate doesn't match the receipt'''
    if not identifier_in_config_profile_info(identifier):
        munkicommon.display_debug2(
            'Profile identifier %s is not installed.' % identifier)
        return True
    receipt_dict = get_profile_receipt(identifier)
    if not receipt_dict:
        munkicommon.display_debug2(
            'No receipt for profile identifier %s.' % identifier)
        return True
    if receipt_dict.get('FileHash') != hash_value:
        munkicommon.display_debug2(
            'Receipt FileHash for profile identifier %s does not match.'
            % identifier)
        return True
    installed_dict = profile_info_for_installed_identifier(identifier)
    if (installed_dict.get('ProfileInstallDate') 
            != receipt_dict.get('ProfileInstallDate')):
        munkicommon.display_debug2(
            'Receipt ProfileInstallDate for profile identifier %s does not '
            'match.' % identifier)
        return True
    return False


def profile_is_installed(identifier):
    '''If identifier is in the output of `profiles -C`
    return True, else return False'''
    if identifier_in_config_profile_info(identifier):
        munkicommon.display_debug2(
            'Profile identifier %s is installed.' % identifier)
        return True
    munkicommon.display_debug2(
        'Profile identifier %s is not installed.' % identifier)
    return False
