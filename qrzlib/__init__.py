#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2024 Fred W6BSD
# All rights reserved.
#
# pylint: disable=consider-using-with

import dbm
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
from importlib.metadata import version
from pathlib import Path
from typing import Tuple
from xml.dom import minidom

__version__ = version("qrzlib")

logging.basicConfig(
  format='%(asctime)s %(name)s:%(lineno)d %(levelname)s - %(message)s',
  level=logging.INFO
)

AGENT = b'Python QRZ API'
URL = "https://xmldata.qrz.com/xml/current/"
DBM_FILE = Path('~', '.local', 'qrz-cache').expanduser()


class DBMCache:
  """Cache decorator used by the QRZ class. It allows multiple runs of
  a program without downloading the call informations from QRZ on
  every run.

  @DBMCache('cachefilename')
  def get_call(callsign):
     . . .

  Cache the call informations in a dbm database. There is no
  mechanism to invalidate the cached information beside removing the
  cache file.

  """

  _EXPIRE_MULT = {
    '': 60,
    'H': 3600,
    'D': 3600 * 24,
    'W': 3600 * 24 * 7,
    'M': 3600 * 24 * 30.5,
    'Y': 3600 * 24 * 7 * 52,
  }

  def __init__(self, dbm_file: Path, expire: str = '1Y'):
    """DBM cache constructor. A cache expiration of 0 mean the data
    cached never expire.
    The expiration time can be expressed with an integer followed by
    the the character [YMWDH] for Year, Month, Week, Days or Hours.

    """
    self.log = logging.getLogger('DBMCache')
    self.log.setLevel(os.getenv('LOG_LEVEL', 'INFO').upper())
    self._dbm_file = dbm_file
    self._create_db()
    self._kexpire = f"_{self.__class__.__name__}_expire_"
    self._expire: float = 0.0

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
      self._expire = _time * DBMCache._EXPIRE_MULT[_mult]
    except KeyError as err:
      raise SystemError(f'Wrong expiration time: "{expire}" - {err}') from None
    self.log.debug(self)

  def _create_db(self):
    if self._dbm_file.exists():
      return

    try:
      if not self._dbm_file.parent.exists():
        self._dbm_file.parent.mkdir()
      with dbm.open(str(self._dbm_file), 'c'):
        pass
    except IOError as err:
      self.log.error(err)
      raise SystemExit(err) from None

  def __repr__(self):
    return f'db: {self._dbm_file} expire: {self._expire}'

  def __len__(self):
    try:
      return len(dbm.open(str(self._dbm_file), 'r'))
    except dbm.error as err:
      raise SystemError(err) from None

  def __contains__(self, key: str):
    try:
      with dbm.open(str(self._dbm_file), 'r') as fdb:
        return key in fdb
    except dbm.error as err:
      logging.error(err)
      raise SystemError(err) from None

  def get_key(self, key: str) -> dict | None:
    try:
      with dbm.open(str(self._dbm_file), 'r') as fdb:
        record = marshal.loads(fdb[key])
        if self._expire == 0 or record[self._kexpire] > time.time() - self._expire:
          del record[self._kexpire]
          self.log.debug('%s found in cache', key)
          return record
        self.log.debug('Cache expired')
        raise KeyError(key)
    except dbm.error as err:
      logging.error(err)
      raise SystemError(err) from None

  def expire(self, key: str) -> bool:
    with dbm.open(str(self._dbm_file), 'c') as fdb:
      if key in fdb:
        del fdb[key]
        return True
    return False

  def store_key(self, key, data) -> None:
    data[self._kexpire] = time.time()
    try:
      with dbm.open(str(self._dbm_file), 'c') as fdb:
        fdb[key] = marshal.dumps(data)
    except dbm.error as err:
      self.log.error(err)
      raise IOError from err

  def __call__(self, func, *args):
    """Simple cache decorator."""
    @wraps(func)
    def gdb_cache(*args):
      key = args[1]
      try:
        record = self.get_key(key)
        return record
      except KeyError:
        self.log.debug('Load %s from QRZ', key)

      try:
        record = func(*args)
        self.store_key(key, record)
      except IOError as err:
        self.log.error(err)
        raise IOError from err
      return record

    return gdb_cache


class QRZ:
  class SessionError(Exception):
    pass

  class NotFound(KeyError):
    pass

  _xml_keys = [
    'call', 'aliases', 'dxcc', 'fname', 'name', 'name_fmt', 'addr1', 'addr2',
    'state', 'zip', 'country', 'ccode', 'lat', 'lon', 'grid', 'county', 'fips',
    'land', 'efdate', 'expdate', 'p_call', 'class', 'codes', 'qslmgr',
    'email', 'url', 'u_views', 'bio', 'image', 'serial', 'moddate', 'MSA',
    'AreaCode', 'TimeZone', 'GMTOffset', 'DST', 'eqsl', 'mqsl', 'cqzone',
    'ituzone', 'geoloc', 'born',
  ]

  def __init__(self) -> None:
    self.log = logging.getLogger('QRZ')
    self.log.setLevel(os.getenv('LOG_LEVEL', 'INFO').upper())
    self.key: bytes | None
    self.error: bytes | None
    self._data: dict = {}

  def authenticate(self, user: str, password: str) -> None:
    url_args = {"username": user.encode('utf-8'), "password": password.encode('utf-8'),
                "agent": AGENT}
    params: bytes = urllib.parse.urlencode(url_args).encode('ascii')

    response = urllib.request.urlopen(URL, params)
    with minidom.parse(response) as dom:
      key = QRZ._getdata(dom, 'Key')
      self.key = key.encode('utf-8') if key else None
      error = QRZ._getdata(dom, 'Error')
      self.error = error.encode('utf-8') if error else None

    if not self.key:
      self.log.error('Authentication error: %s', self.error)
      raise QRZ.SessionError(self.error)

  @DBMCache(DBM_FILE)
  def _get_call(self, callsign: str) -> dict:
    callsign = callsign.upper()
    url_args = {"s": self.key, "callsign": callsign, "agent": AGENT}
    params: bytes = urllib.parse.urlencode(url_args).encode('ascii')

    response = urllib.request.urlopen(URL, params)
    with minidom.parse(response) as dom:
      data = {}
      session = dom.getElementsByTagName('Session')
      call = dom.getElementsByTagName('Callsign')
      if not call:
        error = QRZ._getdata(session[0], 'Error')
        self.log.debug('Not Found: %s', error)
        return {'__qrzlib_error': 'NotFound'}

      for tagname in self._xml_keys:
        data[tagname] = QRZ._getdata(call[0], tagname)
    return data

  def get_call(self, callsign: str):
    if not self.key:
      raise QRZ.SessionError('First authenticate')
    qrz_data = self._get_call(callsign)
    if '__qrzlib_error' in qrz_data:
      self._data = {}
      raise QRZ.NotFound(f"{callsign} {qrz_data['__qrzlib_error']}")

    for tagname, value in qrz_data.items():
      self._data[tagname] = value

  @staticmethod
  def _getdata(dom, nodename: str) -> str | None:
    try:
      data = []
      node = dom.getElementsByTagName(nodename)[0]
      for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
          data.append(child.data)
      return ''.join(data)
    except IndexError:
      return None

  def to_json(self) -> str:
    return json.dumps(self._data)

  def to_dict(self) -> dict:
    return self._data

  @property
  def latlon(self) -> Tuple[float, float] | None:
    if self._data['lat'] and self._data['lon']:
      return (float(self._data['lat']), float(self._data['lon']))
    return None

  @property
  def zip(self) -> str:
    return self._data['zip']

  @property
  def country(self) -> str:
    return self._data['country']

  @property
  def state(self) -> str:
    return self._data['state']

  @property
  def grid(self) -> str:
    return self._data['grid']

  @property
  def fname(self) -> str:
    return self._data['fname']

  @property
  def name(self) -> str:
    return self._data['name']

  @property
  def fullname(self) -> str:
    return self._data['name_fmt']

  @property
  def email(self) -> str:
    return self._data['email']


def main() -> None:
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
