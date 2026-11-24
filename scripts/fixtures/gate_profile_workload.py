"""Generated deterministic workload, exercising current production pure comparison routines."""
from collections import Counter
from hashlib import sha256
from mo2_update_arsenal.file_advisor import NexusFileIndex

def run():
    catalog = {"providerGroups": True, "files": [
        {"fileId": 101, "groupId": 10, "category": 7, "fileName": "old.zip", "name": "Old", "version": "1.0"},
        {"fileId": 102, "groupId": 10, "category": 1, "fileName": "new.zip", "name": "New", "version": "2.0"}]}
    index = NexusFileIndex(catalog)
    digest = sha256()
    counts = Counter()
    for position in range(11325):
        fid, version = ((101, "1.0"), (102, "2.0"), (102, "2.1"))[position % 3]
        row = {"installedFiles": [[17, fid]], "installedVersion": version, "nexusId": 17}
        result = index.resolve(row)
        value = result["verdict"]
        digest.update(f"{position}/{value}/{result['targetFileId']}\n".encode())
        counts[value] += 1
    assert counts == {"candidate": 3775, "current": 3775, "review": 3775}, counts
    return digest.hexdigest()

if __name__ == "__main__":
    actual = [run() for _ in range(3)]
    assert len(set(actual)) == 1
    print("11,325 synthetic rows x3", actual[0])
