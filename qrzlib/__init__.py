#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2025 Fred W6BSD
# All rights reserved.
#
# pylint: disable=consider-using-with

import io
import json
import logging
import os
import pickle
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
from collections.abc import MutableMapping
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
DB_PATH = Path('~', '.local').expanduser()
DB_CACHE = DB_PATH / 'qrz-cache.sqlite3'

DAY = 3600 * 24
WEEK = 3600 * 24 * 7
MONTH = 3600 * 24 * 30.5
YEAR = 3600 * 24 * 7 * 52

CACHE_TABLE = """
PRAGMA synchronous = EXTRA;
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS qrz_cache (
  key TEXT NOT NULL,
  expire TIMESTAMP,
  data BLOB
);
CREATE UNIQUE INDEX if not exists qrz_cache_idx on qrz_cache (key);
"""

DETECT_TYPES = sqlite3.PARSE_DECLTYPES


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


def format_seconds(total_seconds: float) -> str:
  days = int(total_seconds // 86400)
  hours = int((total_seconds % 86400) // 3600)
  minutes = int((total_seconds % 3600) // 60)
  seconds = int(total_seconds % 60)
  return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"


class DBCacheError(Exception):
  pass


class DBCache(MutableMapping):
  def __init__(self, cache_name: str | Path, cache_expire: int = YEAR * 3) -> None:
    self._lock = threading.Lock()
    self.cache_expire = cache_expire
    self.cache_name = cache_name
    self._init_db()

  def connect_db(self, timeout: int = 5) -> sqlite3.Connection:
    conn = sqlite3.connect(self.cache_name, timeout=timeout, check_same_thread=False,
                           detect_types=DETECT_TYPES, isolation_level=None)
    logging.debug("Database: %s", self.cache_name)
    return conn

  def _init_db(self) -> None:
    with self.connect_db() as conn:
      curs = conn.cursor()
      curs.executescript(CACHE_TABLE)

  def __repr__(self) -> str:
    return f'<DBCache: {self.cache_name} {format_seconds(self.cache_expire)}>'

  def __setitem__(self, key: str, data: QRZRecord) -> Any:
    self.put(key, data, None)

  def put(self, key: str, data: QRZRecord, expire: int | None) -> None:
    assert isinstance(key, str)
    _expire = expire if expire is not None else self.cache_expire
    expire = datetime.now() + timedelta(seconds=_expire)

    try:
      with self._lock, self.connect_db() as conn:
        conn.execute("INSERT OR REPLACE INTO qrz_cache (key, expire, data) VALUES (?, ?, ?)",
                     (key, expire, pickle.dumps(data)))
    except pickle.PicklingError as err:
      raise IOError(err) from None

  def seterr(self, key: str, expire: int = 7 * DAY):
    expire = datetime.now() + timedelta(seconds=expire)
    with self._lock, self.connect_db() as conn:
      conn.execute("INSERT OR REPLACE INTO qrz_cache (key, expire, data) VALUES (?, ?, ?)",
                   (key, expire, None))

  def __getitem__(self, key: str) -> None | Any:
    now = datetime.now()

    with self.connect_db() as conn:
      cur = conn.execute("SELECT expire, data FROM qrz_cache WHERE key = ?", (key,))
      if (row := cur.fetchone()) is None:
        raise KeyError(f'{key} not found')

    if row[1] is None:
      raise KeyError(f'{key} not found')

    if row[0] < now or row[1] is None:
      raise KeyError(f'{key} expired')

    return pickle.loads(row[1])

  def __delitem__(self, key: str) -> None:
    with self._lock, self.connect_db() as conn:
      conn.execute("DELETE FROM qrz_cache WHERE key = ?", (key, ))

  def __len__(self) -> int:
    """Count all the records in the cache database"""

    with self.connect_db() as conn:
      cur = conn.execute("SELECT COUNT(*) FROM qrz_cache")
      row = cur.fetchone()
    return row[0]

  def __iter__(self) -> QRZRecord:
    """Iterate through valid items stored in the cache"""
    now = datetime.now()
    with self.connect_db() as conn:
      cur = conn.execute(
        "SELECT expire, data FROM qrz_cache WHERE expire > ? AND data IS NOT NULL", (now,)
      )
      for item in cur.fetchall():
        yield pickle.loads(item[1])

  def purge(self) -> None:
    """Purge all the records that are either expired or NULL (error)"""
    now = datetime.now()
    with self._lock, self.connect_db() as conn:
      conn.execute("DELETE from qrz_cache WHERE expire < ? OR data IS NULL", (now, ))

  def dump(self) -> Any:
    with self.connect_db() as conn:
      cur = conn.execute("SELECT key, expire, data FROM qrz_cache")
      yield cur.fetchall()


class QRZ:
  class SessionError(Exception):
    pass

  class NotFound(KeyError):
    pass

  class XMLError(Exception):
    pass

  def __init__(self, cache_age: int = YEAR * 3, negative_cache_age: int = 2 * MONTH) -> None:
    self.key: bytes | None = None
    self.error: bytes | None = None
    self.count: int | None = None
    self._data: dict = {}
    self.cache_age = cache_age
    self.negative_cache_age = negative_cache_age
    self._cache: DBCache = DBCache(DB_CACHE)

  def __repr__(self) -> str:
    return f'<QRZ: {id(self)}> Cache: {self._cache} Counter: {self.count}'

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
      self.count = int(count) if count else None

    if not self.key:
      raise QRZ.SessionError(self.error)

  def _get_call(self, callsign: str) -> QRZRecord:
    if not isinstance(callsign, str):
      raise ValueError(f'Callsign "{callsign}" should be a string')

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
    if (data := self._cache.get(callsign)):
      return data

    try:
      data = self._get_call(callsign)
      self._cache.put(callsign, data, self.cache_age)
    except KeyError as err:
      self._cache.seterr(callsign, self.negative_cache_age)
      raise QRZ.NotFound(err)
    except ExpatError as err:
      logging.warning('%s - %s', callsign, err)
      self._cache.seterr(callsign, self.negative_cache_age)
      raise QRZ.XMLError(err)

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
  qrz.authenticate(qrz_call, key)

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
