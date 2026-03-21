import subprocess

out = subprocess.check_output(["git", "log", "--format=%aN|%s"]).decode("utf-8")
contribs = {}
for line in out.strip().split("\n"):
    if not line or '|' not in line: continue
    author, msg = line.split('|', 1)
    if author not in contribs:
        contribs[author] = msg # just take the latest contribution as an example
    
with open("CONTRIBUTORS.md", "w") as f:
    f.write("# Contributors\n\nThank you to everyone who has contributed to beacon-skill!\n\n")
    for author in sorted(contribs.keys()):
        if "dependabot" in author.lower() or "action" in author.lower() or "auto" in author.lower():
            continue
        cleaned_msg = contribs[author].replace("'", "").replace("\"", "")
        f.write(f"- **{author}**: {cleaned_msg}\n")
    f.write("- **yuzengbaao (Autonomy System)**: Add AgentHive transport & CONTRIBUTORS.md\n")

print("Created CONTRIBUTORS.md")
