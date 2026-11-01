#!/usr/bin/env python3
"""Entrée historique conservée ; configuration via PLAYSTORE_CONFIG ou --config."""
import sys
from playstoregetreviews.main import main

if __name__ == '__main__':
    raise SystemExit(main(['collect', *sys.argv[1:]]))
