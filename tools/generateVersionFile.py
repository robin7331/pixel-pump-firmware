import argparse
import subprocess
import re
from datetime import datetime, timezone

def get_git_info(repo_path):    
    
    # Note: git describe doesn't work if no tag is available
    try:
        tag = subprocess.check_output(['git', 'describe', '--tags', '--match', 'v[0-9].*', 'HEAD'], cwd=repo_path).decode().strip()
    except subprocess.CalledProcessError as er:
        tag = ""

    try:
      commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path).decode().strip()
    except subprocess.CalledProcessError as er:
      commit_hash = ""

    try:
      branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_path).decode().strip()
    except subprocess.CalledProcessError as er:
          branch = ""

    # Get timestamp of the latest commit
    try:
      commit_timestamp = subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=repo_path).decode().strip()
      commit_datetime = datetime.fromtimestamp(int(commit_timestamp), tz=timezone.utc)
      formatted_timestamp = commit_datetime.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except subprocess.CalledProcessError as er:
      formatted_timestamp = ""

    return tag, commit_hash, branch, formatted_timestamp

def parse_version(tag):
    # "v0.0.8" -> ((0, 0, 8), False); "v0.0.2-1-gb834232" -> ((0, 0, 2), True)
    match = re.match(r'^v(\d+)\.(\d+)\.(\d+)(?:-(\d+)-g[0-9a-f]+)?$', tag)
    if not match:
        return (0, 0, 0), True
    version_tuple = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    dev = match.group(4) is not None
    return version_tuple, dev

def generate_version_file(output_path, repo_path):
    tag, commit_hash, branch, formatted_timestamp = get_git_info(repo_path)
    version_tuple, dev = parse_version(tag)
    print ('Version information:')
    print (f'Tag: {tag}')
    print (f'Version: {version_tuple} (dev build: {dev})')
    print (f'Branch: {branch}')
    print (f'Commit hash: {commit_hash}')
    print (f'Timestamp: {formatted_timestamp}')
    version_file_contents = f'''# Auto-generated version information
tag = "{tag}"
branch = "{branch}"
commit_hash = "{commit_hash}"
timestamp = "{formatted_timestamp}"
version_tuple = {version_tuple}
dev = {dev}
'''

    with open(output_path, 'w') as f:
        f.write(version_file_contents)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate version file')
    parser.add_argument('--output', required=True, help='Path to the output file')
    parser.add_argument('--repo', required=True, help='Path to the repo')
    args = parser.parse_args()

    generate_version_file(args.output, args.repo)