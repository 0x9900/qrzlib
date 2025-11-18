#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2025 Fred W6BSD
# All rights reserved.
#
# pylint: disable=consider-using-with

import dbm
import io
import json
import logging
import os
import pickle
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from getpass import getpass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable
from xml.dom import minidom
from xml.parsers.expat import ExpatError

__version__ = version("qrzlib")

logging.basicConfig(
  format='%(asctime)s %(name)s:%(lineno)d %(levelname)s - %(message)s',
  level=logging.INFO
)

AGENT = b'Python QRZ API - https://github.com/0x9900/qrzlib'
URL = "https://xmldata.qrz.com/xml/current/"
DBM_PATH = Path('~', '.local').expanduser()
DBM_CACHE = DBM_PATH / 'qrz-cache_v2'
DBM_ERROR = DBM_PATH / 'qrz-error_v2'


def mkdate(strdate: str) -> date:
  # 2025-06-17 returns a datetime.date object
  return datetime.strptime(strdate, '%Y-%m-%d').date()


def mkdatetime(strdate: str) -> datetime:
  return datetime.strptime(strdate, '%Y-%m-%d %H:%M:%S')


def mkint(value: str) -> int | None:
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def mkfloat(value: str) -> float | None:
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


class IJSONEncoder(json.JSONEncoder):
  """Special JSON encoder capable of encoding sets"""
  def default(self, o: Any) -> Any:
    if isinstance(o, (date, datetime)):
      return {"__type__": o.__class__.__name__, "value": o.isoformat()}
    return super().default(o)


XML_KEYS: list[tuple[str, Callable]] = [
  ('call', str),
  ('aliases', str),
  ('dxcc', mkint),
  ('fname', str),
  ('name', str),
  ('name_fmt', str),
  ('addr1', str),
  ('addr2', str),
  ('state', str),
  ('zip', str),
  ('country', str),
  ('ccode', mkint),
  ('lat', mkfloat),
  ('lon', mkfloat),
  ('grid', str),
  ('county', str),
  ('fips', str),
  ('land', str),
  ('efdate', mkdate),
  ('expdate', mkdate),
  ('p_call', str),
  ('class', str),
  ('codes', str),
  ('qslmgr', str),
  ('email', str),
  ('url', str),
  ('u_views', mkint),
  ('bio', mkint),
  ('image', str),
  ('serial', mkint),
  ('moddate', mkdatetime),
  ('MSA', str),
  ('AreaCode', str),
  ('TimeZone', str),
  ('GMTOffset', mkint),
  ('DST', str),
  ('eqsl', mkint),
  ('mqsl', mkint),
  ('cqzone', mkint),
  ('ituzone', mkint),
  ('geoloc', str),
  ('born', str)
]


@dataclass
class QRZRecord:
  # pylint: disable=invalid-name, too-many-instance-attributes
  CLASS: str | None
  call: str
  aliases: str | None
  dxcc: int
  fname: str
  name: str
  name_fmt: str
  addr1: str | None
  addr2: str | None
  state: str | None
  zip: str
  country: str
  ccode: int
  lat: float
  lon: float
  grid: str
  county: str
  fips: int
  land: str
  efdate: date
  expdate: date
  p_call: str | None
  codes: str | None
  qslmgr: str | None
  email: str | None
  url: str | None
  u_views: int
  bio: int
  image: str | None
  serial: int
  moddate: datetime
  MSA: str | None
  AreaCode: str | None
  TimeZone: str | None
  GMTOffset: int | None
  DST: str | None
  eqsl: int | None
  mqsl: int | None
  cqzone: int
  ituzone: int
  geoloc: str | None
  born: str | None

  @property
  def latlon(self) -> tuple[float, float] | None:
    if self.lat and self.lon:
      return (self.lat, self.lon)
    return None

  @property
  def fullname(self) -> str:
    return self.name_fmt

  def to_dict(self) -> dict:
    return asdict(self)

  def to_json(self, indent: int = 2) -> str:
    encoder = IJSONEncoder(indent=indent).encode
    return encoder(asdict(self))

  def __contains__(self, field):
    return hasattr(self, field)


@dataclass(frozen=True)
class CacheRecord:
  age: datetime
  data: bytes


@dataclass(frozen=True)
class CacheError:
  age: datetime
  error: str


def format_seconds(total_seconds: float) -> str:
  days = int(total_seconds // 86400)
  hours = int((total_seconds % 86400) // 3600)
  minutes = int((total_seconds % 3600) // 60)
  seconds = int(total_seconds % 60)
  return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"


class DBMCache:
  _EXPIRE_MULTIPLIER = {
    '': 60,
    'H': 3600,
    'D': 3600 * 24,
    'W': 3600 * 24 * 7,
    'M': 3600 * 24 * 30.5,
    'Y': 3600 * 24 * 7 * 52,
  }
  _PARSE_EXPIRE = re.compile(r'^(\d+)(|[YMWDH])$', re.IGNORECASE).match

  def __init__(self, cache_name: str | Path, cache_expire: str = '3Y') -> None:
    assert isinstance(cache_expire, str | Path), 'Cache expiration must be a string'
    assert isinstance(cache_expire, str), 'Cache expiration must be a string'
    self._cache_name = str(cache_name) if isinstance(cache_name, Path) else cache_name

    if not (match := DBMCache._PARSE_EXPIRE(cache_expire)):
      raise SystemError(f'Wrong cache expiration time {cache_expire}')
    _time = int(match.group(1))
    _mult = match.group(2).upper()
    try:
      self._cache_expire = _time * DBMCache._EXPIRE_MULTIPLIER[_mult]
    except KeyError as err:
      raise SystemError(f'Wrong cache expiration time {cache_expire} = {err}') from None

    # Make sure the cache file exists
    try:
      dbm.open(self._cache_name, 'c')
    except dbm.error as err:
      raise IOError(err) from None

  def __repr__(self) -> str:
    return f'<DBMCache: {self._cache_name} {format_seconds(self._cache_expire)}'

  def put(self, key: str, data: Any) -> Any:
    assert isinstance(key, str)
    age = datetime.now()
    _data = CacheRecord(age, data)
    try:
      with dbm.open(self._cache_name, 'c') as fdb:
        fdb[key] = pickle.dumps(_data)
    except dbm.error as err:
      raise IOError(err) from None
    except pickle.PicklingError as err:
      raise IOError(err) from None

  def get(self, key: str) -> None | Any:
    assert isinstance(key, str)
    with dbm.open(self._cache_name, 'r') as fdb:
      _data = fdb.get(key)

    if not _data:
      raise KeyError(key)

    data = pickle.loads(_data)
    if data.age + timedelta(seconds=self._cache_expire) < datetime.now():
      raise KeyError(key)
    return data.data

  def remove(self, key: str) -> None:
    with dbm.open(self._cache_name, 'w') as fdb:
      del fdb[key]

  def __len__(self) -> int:
    with dbm.open(self._cache_name, 'r') as fdb:
      return len(fdb)

  def expiration_date(self, key: str) -> datetime:
    with dbm.open(self._cache_name, 'r') as fdb:
      data = pickle.loads(fdb[key])
    return data.age + timedelta(seconds=self._cache_expire)


class QRZ:
  class SessionError(Exception):
    pass

  class NotFound(KeyError):
    pass

  def __init__(self, cache_age: str = '5Y', negative_cache_age: str = '6M') -> None:
    self.key: bytes | None
    self.error: bytes | None
    self.count: int | None
    self._data: dict = {}
    self._cache: DBMCache = DBMCache(DBM_CACHE, cache_age)
    self._error: DBMCache = DBMCache(DBM_ERROR, negative_cache_age)

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
      count = QRZ._getdata(dom, 'Count')
      self.count = int(count) if key else None

    if not self.key:
      raise QRZ.SessionError(self.error)

  def _get_call(self, callsign: str) -> QRZRecord:
    callsign = callsign.upper()
    url_args = {"s": self.key, "callsign": callsign, "agent": AGENT}

    params = urllib.parse.urlencode(url_args).encode('utf-8')
    response = urllib.request.urlopen(URL, params)
    content = response.read()
    encoding = response.headers.get_content_charset('utf-8')
    try:
      text = content.decode(encoding)
    except UnicodeDecodeError:
      text = content.decode('windows-1252', errors='replace')

    # --- Clean malformed XML --- Remove invalid control characters
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
    # Fix unescaped ampersands (only those not part of an entity)
    text = re.sub(r"&(?![a-zA-Z0-9#]+;)", "&amp;", text)
    with minidom.parse(io.BytesIO(text.encode('utf-8'))) as dom:
      data = {}
      session = dom.getElementsByTagName('Session')
      call = dom.getElementsByTagName('Callsign')
      if not call:
        error = QRZ._getdata(session[0], 'Error')
        raise KeyError(f'{error}')

      for tagname, cast in XML_KEYS:
        if tagname == 'class':
          data[tagname.upper()] = cast(QRZ._getdata(call[0], tagname))
          continue
        try:
          data[tagname] = cast(QRZ._getdata(call[0], tagname))
        except (ValueError, TypeError):
          data[tagname] = None

    return QRZRecord(**data)

  def get_call(self, callsign: str):
    try:
      data = self._cache.get(callsign)
      return data
    except KeyError:
      pass

    try:
      data = self._error.get(callsign)
      return None
    except KeyError:
      pass

    try:
      data = self._get_call(callsign)
      self._cache.put(callsign, data)
    except KeyError as err:
      self._error.put(callsign, str(err))
      return None
    except ExpatError as err:
      logging.warning('%s - %s', callsign, err)
      self._error.put(callsign, str(err))
      return None
    return data

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
      callinfo = qrz.get_call(call)
      print(call, callinfo.fullname, callinfo.zip, callinfo.latlon, callinfo.grid, callinfo.email)
    except QRZ.NotFound as err:
      print(err)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    print("Keyboard Interruption exiting...")
