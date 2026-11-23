"""Single-user development launcher; loads .env without echoing credentials."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
parser=argparse.ArgumentParser()
parser.add_argument('--host',default='127.0.0.1')
parser.add_argument('--port',type=int,default=8001)
parser.add_argument('--cert')
parser.add_argument('--key')
args=parser.parse_args()
if bool(args.cert)!=bool(args.key):
    parser.error('--cert 与 --key 必须一起指定')
p=ROOT/'.env'
if p.exists():
    for line in p.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        key,sep,value=line.partition('=')
        if sep:
            # Existing environment wins; no shell expansion / source execution.
            os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))
import uvicorn
uvicorn.run('app.main:app',host=args.host,port=args.port,access_log=False,
            ssl_certfile=args.cert,ssl_keyfile=args.key)
