#!/usr/bin/env python3
import sys
import logging

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')
    from ttracker.app import run
    sys.exit(run())
