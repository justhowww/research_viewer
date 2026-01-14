import os
import json
import glob
import argparse

# target directory
parser = argparse.ArgumentParser()
parser.add_argument('--target_dir', type=str, default='.', help='Target directory to index')
args = parser.parse_args()

# Extensions to look for
EXTENSIONS = ['*.mp4', '*.webm', '*.mov', '*.png', '*.jpg']

files = []
for ext in EXTENSIONS:
    # recursive=True finds files in subfolders too
    for f in glob.glob(args.target_dir + '**/' + ext, recursive=True):
        # Normalize path separators for web (force /)
        f = f.replace(os.sep, '/')
        if not f.startswith("index_files.py") and not f.endswith(".html"):
            files.append(f)

files.sort()
# append to json instead of overwriting

with open('files.json', 'a') as f:
    json.dump(files, f, indent=2)

print(f"Indexed {len(files)} files into files.json")