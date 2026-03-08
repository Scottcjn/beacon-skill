import subprocess
import os
import sys

def test_install_and_version():
    print("Testing beacon --version...")
    try:
        # We test against the local source
        env = os.environ.copy()
        env["PYTHONPATH"] = "."
        result = subprocess.run(["python3", "-m", "beacon_skill.cli", "--version"], 
                               capture_output=True, text=True, env=env)
        if result.returncode == 0:
            print(f"SUCCESS: {result.stdout.strip()}")
            return True
        else:
            print(f"FAILED: {result.stderr}")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    if test_install_and_version():
        sys.exit(0)
    else:
        sys.exit(1)
