import argparse
import subprocess
import re
from datetime import datetime, timezone

def get_git_info(repo_path):
    tag = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0'], cwd=repo_path).decode().strip()
    tag = tag[1:]  # Remove the first character from the tag
    commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_path).decode().strip()
    branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_path).decode().strip()

    # Extract major, minor, and fix versions from the tag
    version_regex = r'(\d+)\.(\d+)\.(\d+)'
    match = re.match(version_regex, tag)
    if match:
        major = match.group(1)
        minor = match.group(2)
        fix = match.group(3)
    else:
        major = minor = fix = 'Unknown'

    # Get timestamp of the latest commit
    commit_timestamp = subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=repo_path).decode().strip()
    commit_datetime = datetime.fromtimestamp(int(commit_timestamp), tz=timezone.utc)
    formatted_timestamp = commit_datetime.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    return tag, commit_hash, branch, major, minor, fix, formatted_timestamp

def generate_version_file(output_path, repo_path):
    tag, commit_hash, branch, major, minor, fix, formatted_timestamp = get_git_info(repo_path)
    version_file_contents = f'''# Auto-generated version information
tag = "{tag}"
commit_hash = "{commit_hash}"
branch = "{branch}"
major = {major}
minor = {minor}
fix = {fix}
timestamp = "{formatted_timestamp}"
'''

    with open(output_path, 'w') as f:
        f.write(version_file_contents)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate version file')
    parser.add_argument('--output', required=True, help='Path to the output file')
    parser.add_argument('--repo', required=True, help='Path to the repo')
    args = parser.parse_args()

    generate_version_file(args.output, args.repo)