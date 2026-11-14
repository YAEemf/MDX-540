"""Windows print queue control for the Roland MDX-540.

  python queue_ctl.py status            # printer state and jobs (remaining bytes = not yet sent to machine)
  python queue_ctl.py purge             # delete ALL jobs (do this BEFORE cancelling on the machine)
  python queue_ctl.py pause | resume    # pause / resume the printer itself (stops new jobs starting;
                                        # NOTE: does not reliably stop a job already being transferred)
  python queue_ctl.py send FILE [FILE ...]
                                        # refuses unless the queue is empty, sends files in order,
                                        # then resumes the printer.  Moves the real machine.
"""
import sys, os, time, win32print
PRN = 'Roland MDX-540'
ST = {1: 'PAUSED', 2: 'ERROR', 4: 'DELETING', 8: 'SPOOLING', 0x10: 'PRINTING', 0x20: 'OFFLINE',
      0x80: 'PRINTED', 0x1000: 'COMPLETE', 0x2000: 'RETAINED'}

def flags(s): return '|'.join(v for k, v in ST.items() if s & k) or 'QUEUED'

def open_(admin=False):
    return win32print.OpenPrinter(PRN, {'DesiredAccess': win32print.PRINTER_ALL_ACCESS} if admin else None)

def jobs(h): return win32print.EnumJobs(h, 0, 50, 2)

def status():
    h = open_()
    ps = win32print.GetPrinter(h, 2)['Status']
    print(f'printer: {"PAUSED" if ps & 1 else "READY"} (raw {ps})')
    js = jobs(h)
    for j in js:
        print(f"  job {j['JobId']}: {j['pDocument']}  {flags(j['Status'])}  unsent {j['Size']} B")
    if not js: print('  (queue empty)')
    win32print.ClosePrinter(h)

def purge():
    h = open_(True)
    for j in win32print.EnumJobs(h, 0, 50, 1):
        win32print.SetJob(h, j['JobId'], 0, None, win32print.JOB_CONTROL_DELETE)
    win32print.ClosePrinter(h); time.sleep(2); status()

def printer(cmd):
    h = open_(True)
    win32print.SetPrinter(h, 0, None, win32print.PRINTER_CONTROL_PAUSE if cmd == 'pause' else win32print.PRINTER_CONTROL_RESUME)
    win32print.ClosePrinter(h); status()

def send(files):
    h = open_(True)
    if win32print.EnumJobs(h, 0, 50, 1):
        win32print.ClosePrinter(h); sys.exit('queue not empty - refusing to send (run status / purge first)')
    for f in files:
        data = open(f, 'rb').read()
        job = win32print.StartDocPrinter(h, 1, (os.path.basename(f), None, 'RAW'))
        win32print.StartPagePrinter(h); n = win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h); win32print.EndDocPrinter(h)
        print(f'job {job}: queued {n}/{len(data)} B  {os.path.basename(f)}')
    win32print.SetPrinter(h, 0, None, win32print.PRINTER_CONTROL_RESUME)
    win32print.ClosePrinter(h); time.sleep(3); status()

if __name__ == '__main__':
    a = sys.argv[1:] or ['status']
    {'status': lambda: status(), 'purge': lambda: purge(), 'pause': lambda: printer('pause'),
     'resume': lambda: printer('resume'), 'send': lambda: send(a[1:])}[a[0]]()
