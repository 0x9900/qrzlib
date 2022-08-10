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
	qrz.get_call('W6BSD')
	print(qrz.fullname, qrz.zip, qrz.latlon, qrz.grid, qrz.email)
except QRZ.NotFound as err:
	print(err)
```

On the first request the class QRZ get the data from the qrz web
service. Then, by default, the information will be cached forever.

the object QRZ can also return all the fields as a dictionary of as a
json object.

```python
In [6]: qrz.to_dict()
Out[6]:
{'call': 'W6BSD',
 'aliases': 'KM6IGK',
 'dxcc': '291',
 'fname': 'Fred',
 . . .
 'ituzone': '6',
 'geoloc': 'user',
 'born': None}
```
