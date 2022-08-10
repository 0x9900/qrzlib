# qrzlib

Python interface to qrz.com

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
