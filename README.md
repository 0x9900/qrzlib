# qrzlib

Python interface to qrz.com

In order to use this interface you need to have a valid Ham radio
license and a qrz.com account.

## Usage

```python
import qrzlib

qrz = qrzlib.QRZ()
qrz.authenticate('qrz-id', 'xmldata-key')
try:
	call_info = qrz.get_call('W6BSD')
	print(call_info.fullname, call_info.latlon, call_info.grid, call_info.email)
except QRZ.NotFound as err:
	print(err)
```

On the first request the class QRZ get the data from the qrz web
service. Then, by default, the information will be cached forever.

the object QRZ can also return all the fields as a dictionary of as a
json object.

```python
>>> call_info.to_dict()
{'CLASS': 'E',
 'call': 'W6BSD',
 'aliases': 'KM6IGK',
 'dxcc': 291,
 'fname': 'Fred',
 'ccode': 271,
 'lat': 37.460659,
 'lon': -95.543333,
 'grid': 'EM27fl',
 . . .
 'expdate': datetime.date(2027, 3, 3),
 'cqzone': 3,
 'ituzone': 6,
 }
```
