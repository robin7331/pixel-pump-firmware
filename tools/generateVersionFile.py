import argparse
import subprocess
import re
from datetime import datetime, timezone

def get_git_info():
    tag = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0']).decode().strip()
    tag = tag[1:]  # Remove the first character from the tag
    commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode().strip()

    # Get timestamp of the latest commit
    commit_timestamp = subprocess.check_output(['git', 'log', '-1', '--format=%ct']).decode().strip()
    commit_datetime = datetime.fromtimestamp(int(commit_timestamp), tz=timezone.utc)
    formatted_timestamp = commit_datetime.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') 

    # Extract major, minor, and fix versions from the tag
    version_regex = r'(\d+)\.(\d+)\.(\d+)'
    match = re.match(version_regex, tag)
    if match:
        major = match.group(1)
        minor = match.group(2)
        fix = match.group(3)
    else:
        major = minor = fix = 'Unknown'

    return tag, commit_hash, branch, major, minor, fix, formatted_timestamp

def generate_version_file(output_path):
    tag, commit_hash, branch, major, minor, fix, timestamp = get_git_info()
    version_file_contents = f'''# Auto-generated version information
tag = "{tag}"
major = {major}
minor = {minor}
fix = {fix}
branch = "{branch}"
commit_date = "{timestamp}"
commit_hash = "{commit_hash}"
'''

    with open(output_path, 'w') as f:
        f.write(version_file_contents)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate version file')
    parser.add_argument('--output', required=True, help='Path to the output file')
    args = parser.parse_args()

    generate_version_file(args.output)
