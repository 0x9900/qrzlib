# qrzlib

Python interface to qrz.com

In order to use this interface you need to have a valid Ham radio
license and a qrz.com account.

## Usage

```python
import os
import qrzlib
qrz = qrzlib.QRZ()
qrz.authenticate(os.getenv('QRZ_USER'), os.getenv('QRZ_PASSWORD'))
try:
	call_info = qrz.get_call('W6BSD')
	print(call_info.fullname, call_info.latlon, call_info.grid, call_info.email)
except qrzlib.QRZ.NotFound as err:
	print(err)
```

On the first request the class QRZ get the data from the qrz web
service. Then, by default, the information will be cached forever.

the object QRZ can also return all the fields as a dictionary of as a
json object.

```python
>>> callinfo.to_dict()
{
    'TimeZone': 'Central',
    'aliases': 'KM6IGK',
    'call': 'W6BSD',
    'ccode': 271,
    'class': 'E',
    'country': 'United States',
    'county': 'Harris',
    'cqzone': 3,
    'dxcc': 291,
    'fname': 'Fred',
    'grid': 'EM27fl',
    'image': 'https://cdn-xml.qrz.com/d/w6bsd/FredSailing_jpeg.jpg',
    'ituzone': 6,
    'land': 'United States',
    . . .
}
>>>
```
