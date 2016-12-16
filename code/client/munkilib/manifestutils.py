#!/usr/bin/python
# encoding: utf-8
#
# Copyright 2009-2016 Greg Neagle.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
manifestutils.py

Created by Greg Neagle on 2016-12-16.


Functions for working with manifest files
"""

import os
import urllib2
from OpenSSL.crypto import load_certificate, FILETYPE_PEM

from . import fetch
from . import munkicommon
from . import FoundationPlist


class ManifestException(Exception):
    """Lets us raise an exception when we can't get a manifest."""
    pass


class ManifestInvalidException(ManifestException):
    """Lets us raise an exception when we get an invalid manifest."""
    pass


class ManifestNotRetrievedException(ManifestException):
    """Lets us raise an exception when manifest is not retrieved."""
    pass


class ManifestServerConnectionException(ManifestException):
    """Exception for connection error."""
    pass


def getmanifest(manifest_name, suppress_errors=False):
    """Gets a manifest from the server.

    Returns:
      string local path to the downloaded manifest, or None
    Raises:
      fetch.ConnectionError if we can't connect to the server
      ManifestException if we can't get the manifest
    """
    if manifest_name in MANIFESTS:
        return MANIFESTS[manifest_name]

    manifestbaseurl = (munkicommon.pref('ManifestURL') or
                       munkicommon.pref('SoftwareRepoURL') + '/manifests/')
    if (not manifestbaseurl.endswith('?') and
            not manifestbaseurl.endswith('/')):
        manifestbaseurl = manifestbaseurl + '/'
    manifest_dir = os.path.join(munkicommon.pref('ManagedInstallDir'),
                                'manifests')

    manifestdisplayname = manifest_name
    manifesturl = (
        manifestbaseurl + urllib2.quote(manifest_name.encode('UTF-8')))

    munkicommon.display_debug2('Manifest base URL is: %s', manifestbaseurl)
    munkicommon.display_detail('Getting manifest %s...', manifestdisplayname)
    manifestpath = os.path.join(manifest_dir, manifest_name)

    # Create the folder the manifest shall be stored in
    destinationdir = os.path.dirname(manifestpath)
    try:
        os.makedirs(destinationdir)
    except OSError, err:
        # OSError will be raised if destinationdir exists, ignore this case
        if not os.path.isdir(destinationdir):
            if not suppress_errors:
                munkicommon.display_error(
                    'Could not create folder to store manifest %s: %s',
                    manifestdisplayname, err
                )
            raise ManifestException(err)

    message = 'Retrieving list of software for this machine...'
    try:
        dummy_value = fetch.munki_resource(
            manifesturl, manifestpath, message=message)
    except fetch.ConnectionError, err:
        raise ManifestServerConnectionException(err)
    except fetch.Error, err:
        if not suppress_errors:
            munkicommon.display_error(
                'Could not retrieve manifest %s from the server: %s',
                manifestdisplayname, err)
        raise ManifestNotRetrievedException(err)

    try:
        # read plist to see if it is valid
        dummy_data = FoundationPlist.readPlist(manifestpath)
    except FoundationPlist.NSPropertyListSerializationException:
        errormsg = 'manifest returned for %s is invalid.' % manifestdisplayname
        munkicommon.display_error(errormsg)
        try:
            os.unlink(manifestpath)
        except (OSError, IOError):
            pass
        raise ManifestInvalidException(errormsg)
    else:
        # plist is valid
        MANIFESTS[manifest_name] = manifestpath
        return manifestpath


def get_primary_manifest(alternate_id=''):
    """Gets the client manifest from the server."""
    manifest = ""
    manifesturl = (munkicommon.pref('ManifestURL') or
                   munkicommon.pref('SoftwareRepoURL') + '/manifests/')
    if not manifesturl.endswith('?') and not manifesturl.endswith('/'):
        manifesturl = manifesturl + '/'
    munkicommon.display_debug2('Manifest base URL is: %s', manifesturl)

    clientidentifier = alternate_id or munkicommon.pref('ClientIdentifier')

    if not alternate_id and munkicommon.pref('UseClientCertificate') and \
        munkicommon.pref('UseClientCertificateCNAsClientIdentifier'):
        # we're to use the client cert CN as the clientidentifier
        if munkicommon.pref('UseClientCertificate'):
            # find the client cert
            client_cert_path = munkicommon.pref('ClientCertificatePath')
            if not client_cert_path:
                for name in ['cert.pem', 'client.pem', 'munki.pem']:
                    client_cert_path = os.path.join(
                        munkicommon.pref('ManagedInstallDir'), 'certs', name)
                    if os.path.exists(client_cert_path):
                        break
            if client_cert_path and os.path.exists(client_cert_path):
                fileobj = open(client_cert_path)
                data = fileobj.read()
                fileobj.close()
                x509 = load_certificate(FILETYPE_PEM, data)
                clientidentifier = x509.get_subject().commonName

    if clientidentifier:
        manifest = getmanifest(
            urllib2.quote(clientidentifier.encode('UTF-8')))
    else:
        # no client identifier specified, so use the hostname
        hostname = os.uname()[1]
        # there shouldn't be any characters in a hostname that need quoting,
        # but see https://code.google.com/p/munki/issues/detail?id=276
        clientidentifier = urllib2.quote(hostname)
        munkicommon.display_detail(
            'No client id specified. Requesting %s...', clientidentifier)
        try:
            manifest = getmanifest(clientidentifier, suppress_errors=True)
        except ManifestNotRetrievedException:
            pass

        if not manifest:
            # try the short hostname
            clientidentifier = urllib2.quote(hostname.split('.')[0])
            munkicommon.display_detail(
                'Request failed. Trying %s...', clientidentifier)
            try:
                manifest = getmanifest(
                    clientidentifier, suppress_errors=True)
            except ManifestNotRetrievedException:
                pass

        if not manifest:
            # try the machine serial number
            clientidentifier = urllib2.quote(
                munkicommon.getMachineFacts()['serial_number'])
            if clientidentifier != 'UNKNOWN':
                munkicommon.display_detail(
                    'Request failed. Trying %s...', clientidentifier)
                try:
                    manifest = getmanifest(
                        clientidentifier, suppress_errors=True)
                except ManifestNotRetrievedException:
                    pass

        if not manifest:
            # last resort - try for the site_default manifest
            clientidentifier = 'site_default'
            munkicommon.display_detail(
                'Request failed. Trying %s...', clientidentifier)

    # record this info for later
    # primary manifest is tagged as "primary_manifest"
    MANIFESTS['primary_manifest'] = manifest
    munkicommon.report['ManifestName'] = clientidentifier
    munkicommon.display_detail('Using manifest: %s', clientidentifier)
    return manifest


def clean_up_manifests():
    """Removes any manifest files that are no longer in use by this client"""
    manifest_dir = os.path.join(
        munkicommon.pref('ManagedInstallDir'), 'manifests')

    exceptions = [
        "SelfServeManifest"
    ]

    for (dirpath, dirnames, filenames) in os.walk(manifest_dir, topdown=False):
        for name in filenames:

            if name in exceptions:
                continue

            abs_path = os.path.join(dirpath, name)
            rel_path = abs_path[len(manifest_dir):].lstrip("/")

            if rel_path not in MANIFESTS.keys():
                os.unlink(abs_path)

        # Try to remove the directory
        # (rmdir will fail if directory is not empty)
        try:
            if dirpath != manifest_dir:
                os.rmdir(dirpath)
        except OSError:
            pass


def get_manifest_data(manifestpath):
    '''Reads a manifest file, returns a dictionary-like object.'''
    plist = {}
    try:
        plist = FoundationPlist.readPlist(manifestpath)
    except FoundationPlist.NSPropertyListSerializationException:
        munkicommon.display_error('Could not read plist: %s', manifestpath)
        if os.path.exists(manifestpath):
            try:
                os.unlink(manifestpath)
            except OSError, err:
                munkicommon.display_error(
                    'Failed to delete plist: %s', unicode(err))
        else:
            munkicommon.display_error('plist does not exist.')
    return plist


def get_manifest_value_for_key(manifestpath, keyname):
    """Returns a value for keyname in manifestpath"""
    plist = get_manifest_data(manifestpath)
    try:
        return plist.get(keyname, None)
    except AttributeError, err:
        munkicommon.display_error(
            'Failed to get manifest value for key: %s (%s)',
            manifestpath, keyname)
        munkicommon.display_error(
            'Manifest is likely corrupt: %s', unicode(err))
        return None


# module globals
MANIFESTS = {}

if __name__ == '__main__':
    print 'This is a library of support tools for the Munki Suite.'
