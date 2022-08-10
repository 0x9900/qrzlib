#!/usr/bin/env python3.9
#

import _gdbm as gdbm
import json
import logging
import marshal
import os
import re
import time
import urllib.parse
import urllib.request

from functools import wraps
from getpass import getpass
from xml.dom import minidom

logging.basicConfig(
  format='%(asctime)s %(name)s:%(lineno)d %(levelname)s - %(message)s',
  level=logging.INFO
)

AGENT = 'Python QRZ API'
URL = "https://xmldata.qrz.com/xml/current/"
DBM_FILE = '/Users/fred/.local/qrz-cache.gdbm'

class GDBMCache:
  """Cache decorator used by the QRZ class. It allows multiple runs of
  a program without downloading the call informations from QRZ on
  every run.

  @GDBMCache('cachefilename.gdbm')
  def get_call(callsign):
     . . .

  Cache the call informations in a gdbm database. There is no
  mechanism to invalidate the cached information beside removing the
  cache file.

  """

  _EXPIRE_MULT = {
    '': 1,
    'H': 3600,
    'D': 3600 * 24,
    'W': 3600 * 24 * 7,
    'M': 3600 * 24 * 30.5,
    'Y': 3600 * 24 * 7 * 52,
    }

  def __init__(self, dbm_file, expire=0):
    """GDBM cache constructor. A cache expiration of 0 mean the data
    cached never expire.
    The expiration time can be expressed with an integer followed by
    the the character [YMWDH] for Year, Month, Week, Days or Hours.

    """
    self.log = logging.getLogger('GDBMCache')
    self._dbm_file = dbm_file
    self._kexpire = f"_{self.__class__.__name__}_expire_"
    if isinstance(expire, int):
      self._expire = expire
      return
    if not isinstance(expire, str):
      raise SystemError('Expiration time error')

    match = re.match(r'^(\d+)(|[YMWDH])$', expire, re.IGNORECASE)
    if not match:
      raise SystemError('Expiration time error')
    _time = int(match.group(1))
    _mult = match.group(2).upper()
    try:
      self._expire = _time * GDBMCache._EXPIRE_MULT[_mult]
    except KeyError as err:
      raise SystemError(f'Wrong expiration time: "{expire}" - {err}') from None
    self.log.debug(self)

  def __repr__(self):
    return f'db: {self._dbm_file} expire: {self._expire}'

  def __call__(self, func, *args):
    """Simple cache decorator."""
    @wraps(func)
    def gdb_cache(*args):
      key = args[1]
      try:
        with gdbm.open(self._dbm_file, 'r') as fdb:
          record = marshal.loads(fdb[key])
        if self._expire == 0 or record[self._kexpire] > time.time() - self._expire:
          del record[self._kexpire]
          return record
        self.log.debug('Cache expired')
        raise KeyError
      except gdbm.error as err:
        logging.error(err)
      except KeyError:
        pass

      assert not bool(self.log.debug('Load %s from QRZ', key))
      try:
        record = func(*args)
        record[self._kexpire] = time.time()
        with gdbm.open(self._dbm_file, 'c') as fdb:
          fdb[key] = marshal.dumps(record)
      except gdbm.error as err:
        self.log.error(err)
        raise IOError from err
      return record

    return gdb_cache

class QRZ:
  class SessionError(Exception):
    pass

  class NotFound(Exception):
    pass

  _xml_keys = [
    'call', 'aliases', 'dxcc', 'fname', 'name', 'name_fmt', 'addr1', 'addr2',
    'state', 'zip', 'country', 'ccode', 'lat', 'lon', 'grid', 'county', 'fips',
    'land', 'efdate', 'expdate', 'p_call', 'class', 'codes', 'qslmgr',
    'email', 'url', 'u_views', 'bio', 'image', 'serial', 'moddate', 'MSA',
    'AreaCode', 'TimeZone', 'GMTOffset', 'DST', 'eqsl', 'mqsl', 'cqzone',
    'ituzone', 'geoloc', 'born',
  ]

  def __init__(self):
    self.log = logging.getLogger('QRZ')
    self.key = None
    self.error = None
    self._data = {}

  def authenticate(self, user, password):
    params = dict(username=user, password=password, agent=AGENT)
    params = urllib.parse.urlencode(params).encode('ascii')

    response = urllib.request.urlopen(URL, params)
    with minidom.parse(response) as dom:
      self.key = QRZ.getdata(dom, 'Key')
      self.error = QRZ.getdata(dom, 'Error')

    if not self.key:
      self.log.error('Authentication error: %s', self.error)
      raise QRZ.SessionError(self.error)

  @GDBMCache(DBM_FILE)
  def _get_call(self, callsign):
    callsign = callsign.upper()
    params = dict(s=self.key, callsign=callsign, agent=AGENT)
    params = urllib.parse.urlencode(params).encode('ascii')

    response = urllib.request.urlopen(URL, params)
    with minidom.parse(response) as dom:
      data = {}
      session = dom.getElementsByTagName('Session')
      callsign = dom.getElementsByTagName('Callsign')
      if not callsign:
        error = QRZ.getdata(session[0], 'Error')
        raise QRZ.NotFound(error)

      for tagname in self._xml_keys:
        data[tagname] = QRZ.getdata(callsign[0], tagname)
    return data

  def get_call(self, callsign):
    if not self.key:
      raise QRZ.SessionError('First authenticate')
    qrz_data = self._get_call(callsign)
    for tagname, value in qrz_data.items():
      self._data[tagname] = value

  @staticmethod
  def getdata(dom, nodename):
    try:
      data = []
      node = dom.getElementsByTagName(nodename)[0]
      for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
          data.append(child.data)
      return ''.join(data)
    except IndexError:
      return None

  def to_json(self):
    return json.dumps(self._data)

  def to_dict(self):
    return self._data

  @property
  def latlon(self):
    if self._data['lat'] and self._data['lon']:
      return (float(self._data['lat']), float(self._data['lon']))
    return None

  @property
  def zip(self):
    return self._data['zip']

  @property
  def grid(self):
    return self._data['grid']

  @property
  def fname(self):
    return self._data['fname']

  @property
  def name(self):
    return self._data['name']

  @property
  def fullname(self):
    return self._data['name_fmt']

  @property
  def email(self):
    return self._data['email']


def main():
  qrz = QRZ()
  qrz_call = os.getenv('QRZ_CALL', 'W6BSD')
  key = os.getenv('QRZ_KEY') or getpass(f'"{qrz_call}" XML Data key: ')
  qrz.authenticate('W6BSD', key)
  while True:
    try:
      call = input('Callsign: ')
      call = call.strip().upper()
      if not call:
        continue
    except EOFError:
      break
    if call in ('QUIT', 'EXIT', 'BYE'):
      break
    try:
      qrz.get_call(call)
      print(call, qrz.fullname, qrz.zip, qrz.latlon, qrz.grid, qrz.email)
    except QRZ.NotFound as err:
      print(err)

if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    print("Keyboard Interruption exiting...")
