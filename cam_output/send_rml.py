"""Send an RML file RAW to the Roland MDX-540 Windows printer queue.  usage: python send_rml.py FILE"""
import sys, win32print
PRN = 'Roland MDX-540'
data = open(sys.argv[1], 'rb').read()
h = win32print.OpenPrinter(PRN)
try:
    job = win32print.StartDocPrinter(h, 1, (sys.argv[1].split('/')[-1], None, 'RAW'))
    win32print.StartPagePrinter(h)
    n = win32print.WritePrinter(h, data)
    win32print.EndPagePrinter(h)
    win32print.EndDocPrinter(h)
    print(f'job {job}: sent {n} of {len(data)} bytes to {PRN}')
finally:
    win32print.ClosePrinter(h)
