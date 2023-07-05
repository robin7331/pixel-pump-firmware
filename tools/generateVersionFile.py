import argparse
import subprocess
import re
from datetime import datetime, timezone

def get_latest_version_tag(repo_path):
    git_process = subprocess.Popen(['git', '-C', repo_path, 'tag', '--list', 'v*'], stdout=subprocess.PIPE)
    tags = git_process.communicate()[0].decode().strip().split('\n')
    version_tags = [tag for tag in tags if tag.startswith('v')]
    if not version_tags:
        return None
    latest_tag = max(version_tags)
    return latest_tag


def get_git_info(repo_path):    
    
    # Note: git describe doesn't work if no tag is available
    try:
        tag = subprocess.check_output(['git', 'describe', '--tags', '--abbrev=0', 'HEAD'], cwd=repo_path).decode().strip()
    except subprocess.CalledProcessError as er:
        tag = ""
    
    try:
      # Get the latest version tag or "" if no tag is available
      latest_version_tag = get_latest_version_tag(repo_path)
      if latest_version_tag is None:
          latest_version_tag = ""
    except subprocess.CalledProcessError as er:
      latest_version_tag = ""

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

    return tag, latest_version_tag, commit_hash, branch, formatted_timestamp

def generate_version_file(output_path, repo_path):
    tag, latest_version_tag, commit_hash, branch, formatted_timestamp = get_git_info(repo_path)
    print ('Version information:')
    print (f'Tag: {tag}')
    print (f'Latest Version Tag: {latest_version_tag}')
    print (f'Branch: {branch}')
    print (f'Commit hash: {commit_hash}')
    print (f'Timestamp: {formatted_timestamp}')
    version_file_contents = f'''# Auto-generated version information
tag = "{tag}"
latest_version_tag = "{latest_version_tag}"
branch = "{branch}"
commit_hash = "{commit_hash}"
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