#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2025 Fred W6BSD
# All rights reserved.
#
# pylint: disable=consider-using-with

import enum
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
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
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
WEEK = DAY * 7
MONTH = int(DAY * 30.5)   # Roughly a month
YEAR = int(MONTH * 12)    # Roughly a year

CACHE_TABLE = """
PRAGMA synchronous = EXTRA;
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS qrz_cache (
  key TEXT NOT NULL,
  expire TIMESTAMP,
  status INTEGER,
  data BLOB
);
CREATE UNIQUE INDEX if not exists qrz_cache_idx on qrz_cache (key);
"""

DETECT_TYPES = sqlite3.PARSE_DECLTYPES


def mkdate(strdate: str) -> date:
  # 2025-06-17 returns a datetime.date object
  return datetime.strptime(strdate, '%Y-%m-%d').date()


def mkstr(value: str) -> str | None:
  if isinstance(value, str) and value != 'None':
    return str(value)
  return None


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
  ('call', mkstr),
  ('aliases', mkstr),
  ('dxcc', mkint),
  ('fname', mkstr),
  ('name', mkstr),
  ('name_fmt', mkstr),
  ('addr1', mkstr),
  ('addr2', mkstr),
  ('state', mkstr),
  ('zip', mkstr),
  ('country', mkstr),
  ('ccode', mkint),
  ('lat', mkfloat),
  ('lon', mkfloat),
  ('grid', mkstr),
  ('county', mkstr),
  ('fips', mkstr),
  ('land', mkstr),
  ('efdate', mkdate),
  ('expdate', mkdate),
  ('p_call', mkstr),
  ('class', mkstr),
  ('codes', mkstr),
  ('qslmgr', mkstr),
  ('email', mkstr),
  ('url', mkstr),
  ('u_views', mkint),
  ('bio', mkint),
  ('image', mkstr),
  ('serial', mkint),
  ('moddate', mkdatetime),
  ('MSA', mkstr),
  ('AreaCode', mkstr),
  ('TimeZone', mkstr),
  ('GMTOffset', mkint),
  ('DST', mkstr),
  ('eqsl', mkint),
  ('mqsl', mkint),
  ('cqzone', mkint),
  ('ituzone', mkint),
  ('geoloc', mkstr),
  ('born', mkstr)
]


@dataclass
class QRZRecord:
  # pylint: disable=invalid-name, too-many-instance-attributes
  call: str
  aliases: str | None
  dxcc: int | None
  fname: str
  name: str
  name_fmt: str
  addr1: str | None
  addr2: str | None
  state: str | None
  zip: str
  country: str
  ccode: int | None
  lat: float
  lon: float
  grid: str
  county: str
  fips: int | None
  land: str
  efdate: date
  expdate: date
  p_call: str | None
  class_: str | None
  codes: str | None
  qslmgr: str | None
  email: str | None
  url: str | None
  u_views: int | None
  bio: int | None
  image: str | None
  serial: int | None
  moddate: datetime
  MSA: str | None
  AreaCode: str | None
  TimeZone: str | None
  GMTOffset: int | None
  DST: str | None
  eqsl: int | None
  mqsl: int | None
  cqzone: int | None
  ituzone: int | None
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


class RecordStatus(enum.IntEnum):
  ACTIVE = 1
  ERROR = 2


class DBCacheError(Exception):
  pass


class DBCache(MutableMapping):
  def __init__(self, cache_name: str | Path, cache_expire: int = YEAR * 3) -> None:
    self._lock = threading.Lock()
    self.cache_name = cache_name
    self.cache_expire = cache_expire
    self.cache = self.connect_db()
    self._init_db()

  def connect_db(self, timeout: int = 5) -> sqlite3.Connection:
    conn = sqlite3.connect(self.cache_name, timeout=timeout, check_same_thread=False,
                           detect_types=DETECT_TYPES, isolation_level=None)
    logging.debug("Database: %s", self.cache_name)
    return conn

  def _init_db(self) -> None:
    with self._cursor() as cur:
      cur.executescript(CACHE_TABLE)

  def __repr__(self) -> str:
    return f'<DBCache: {self.cache_name} {format_seconds(self.cache_expire)}>'

  def close(self) -> None:
    with self._lock:
      self.cache.close()

  def __enter__(self) -> "DBCache":
    return self

  def __exit__(self, *exc: Any) -> None:
    self.close()

  @contextmanager
  def _cursor(self) -> Iterator[sqlite3.Cursor]:
    """Serialize all access to the shared connection, reads and writes alike"""
    with self._lock:
      cur = self.cache.cursor()
      try:
        yield cur
      finally:
        cur.close()

  def __setitem__(self, key: str, data: QRZRecord) -> Any:
    self.put(key, data, RecordStatus.ACTIVE, None)

  def put(self,
          key: str,
          data: str | QRZRecord,
          status: RecordStatus,
          expire: int | None) -> None:
    if not isinstance(key, str):
      raise TypeError(f'The key mist be a str, got a {type(key)!r}')

    expire = expire if expire is not None else self.cache_expire
    _expire = datetime.now() + timedelta(seconds=expire)

    try:
      with self._cursor() as cur:
        cur.execute(
          "INSERT OR REPLACE INTO qrz_cache (key, expire, status, data) VALUES (?, ?, ?, ?)",
          (key, _expire, status, pickle.dumps(data))
        )
    except pickle.PicklingError as err:
      raise IOError(err) from err
    except sqlite3.OperationalError as err:
      raise DBCacheError(err) from err

  def __getitem__(self, key: str) -> None | Any:
    now = datetime.now()

    with self._cursor() as cur:
      cur.execute("SELECT status, expire, data FROM qrz_cache WHERE key = ?", (key,))
      if (row := cur.fetchone()) is None:
        raise KeyError(f'{key} not found')
    status, expire, data = row

    if status == RecordStatus.ERROR:
      raise KeyError(f'{key} not found')

    if expire < now:
      raise KeyError(f'{key} expired')

    return pickle.loads(data)

  def __delitem__(self, key: str) -> None:
    with self._cursor() as cur:
      cur.execute("DELETE FROM qrz_cache WHERE key = ?", (key, ))
      if cur.rowcount == 0:
        raise KeyError(f'{key} not found')

  def __len__(self) -> int:
    """Count all the valid records in the cache database"""
    now = datetime.now()

    with self._cursor() as cur:
      cur.execute(
        "SELECT COUNT(*) FROM qrz_cache WHERE expire > ? and status == ?",
        (now, RecordStatus.ACTIVE)
      )
      row = cur.fetchone()
    return row[0]

  def __iter__(self) -> Iterator[str]:
    """Iterate through valid items stored in the cache"""
    now = datetime.now()

    with self._cursor() as cur:
      cur.execute(
        "SELECT key FROM qrz_cache WHERE expire > ? AND status == ?",
        (now, RecordStatus.ACTIVE)
      )
      keys = [item[0] for item in cur]
    yield from keys

  def purge(self) -> None:
    """Purge all the records that are either expired or status error"""
    now = datetime.now()

    with self._cursor() as cur:
      cur.execute("DELETE from qrz_cache WHERE expire < ? OR status != ?",
                  (now, RecordStatus.ACTIVE))

  def dump(self) -> Iterator[tuple[str, datetime, int, QRZRecord]]:
    with self._cursor() as cur:
      cur.execute("SELECT key, expire, status, data FROM qrz_cache")
      rows = cur.fetchall()
    for item in rows:
      yield (*item[0:3], pickle.loads(item[3]))

  def count_all(self) -> dict[str, float]:
    """Count all the records grouped by status"""
    with self._cursor() as cur:
      cur.execute("SELECT status, count(*) FROM qrz_cache GROUP BY status")
      row = cur.fetchall()

    counts = {}
    for status, count in row:
      counts[RecordStatus(status).name] = count

    return counts


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
    self.cache_age = cache_age
    self.negative_cache_age = negative_cache_age
    self._cache: DBCache = DBCache(DB_CACHE)

  def __repr__(self) -> str:
    return f'<QRZ: {id(self)}> Cache: {self._cache} Counter: {self.count}'

  def authenticate(self, user: str, password: str, timeout: int = 10) -> None:
    url_args = {
      "username": user.encode('utf-8'),
      "password": password.encode('utf-8'),
      "agent": AGENT,
    }
    params: bytes = urllib.parse.urlencode(url_args).encode('ascii')

    try:
      with urllib.request.urlopen(URL, params, timeout=timeout) as response:
        dom = minidom.parse(response)
    except (urllib.error.URLError, urllib.error.HTTPError) as err:
      raise QRZ.SessionError(f'Authentication request failed: {err}') from err
    except ExpatError as err:
      raise QRZ.SessionError(f'Malformed response from QRZ: {err}') from err

    key = QRZ._getdata(dom, 'Key')
    self.key = key.encode('utf-8') if key else None
    error = QRZ._getdata(dom, 'Error')
    self.error = error.encode('utf-8') if error else None
    count = QRZ._getdata(dom, 'Count')
    self.count = int(count) if count else None

    if not self.key:
      raise QRZ.SessionError(self.error.decode('utf-8') if self.error else 'Unknown error')

  def _get_call(self, callsign: str, timeout: int = 10) -> QRZRecord:
    if not isinstance(callsign, str):
      raise ValueError(f'Callsign "{callsign}" should be a string')

    if not self.key:
      raise QRZ.SessionError('Not authenticated: call authenticate() first')

    callsign = callsign.upper()
    url_args = {"s": self.key, "callsign": callsign, "agent": AGENT}
    params = urllib.parse.urlencode(url_args).encode('utf-8')

    try:
      with urllib.request.urlopen(URL, params, timeout=timeout) as response:
        content = response.read()
        encoding = response.headers.get_content_charset('utf-8')
    except (urllib.error.URLError, urllib.error.HTTPError) as err:
      raise QRZ.SessionError(f'Lookup request failed for "{callsign}": {err}') from err

    try:
      text = content.decode(encoding)
    except UnicodeDecodeError:
      text = content.decode('windows-1252', errors='replace')

    data = self._parse_dom(text)
    # Ignore due to mypy limitation
    return QRZRecord(**data)  # type: ignore[arg-type]

  def get_call(self, callsign: str):
    if (data := self._cache.get(callsign)):
      return data

    try:
      data = self._get_call(callsign)
      self._cache.put(callsign, data, RecordStatus.ACTIVE, self.cache_age)
    except KeyError as err:
      self._cache.put(callsign, str(err), RecordStatus.ERROR, self.negative_cache_age)
      raise QRZ.NotFound(err) from err
    except ExpatError as err:
      logging.warning('%s - %s', callsign, err)
      self._cache.put(callsign, str(err), RecordStatus.ERROR, self.negative_cache_age)
      raise QRZ.XMLError(err) from err

    return data

  @staticmethod
  def _parse_dom(text: str) -> dict[str, str | int | float | datetime | None]:
    # --- Clean malformed XML --- Remove invalid control characters
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
    # Fix unescaped ampersands (only those not part of an entity)
    text = re.sub(r"&(?![a-zA-Z0-9#]+;)", "&amp;", text)

    dom = minidom.parse(io.BytesIO(text.encode('utf-8')))
    data = {}
    session = dom.getElementsByTagName('Session')
    call = dom.getElementsByTagName('Callsign')
    if not call:
      error = QRZ._getdata(session[0], 'Error') if session else 'Unknown error'
      raise KeyError(f'{error}')

    for tagname, cast in XML_KEYS:
      try:
        value = cast(QRZ._getdata(call[0], tagname))
        if tagname == 'class':
          tagname = 'class_'
        data[tagname] = value
      except (ValueError, TypeError):
        data[tagname] = None

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
