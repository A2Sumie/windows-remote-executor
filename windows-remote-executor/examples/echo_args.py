import json
import os
import sys


print(
    json.dumps(
        {
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "python": sys.executable,
        },
        ensure_ascii=False,
    )
)
