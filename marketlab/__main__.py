import sys
from . import radar, evaluate

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "test":
        evaluate.run()
    else:
        day = next((x.split("=", 1)[1] for x in a if x.startswith("date=")), None)
        radar.run(day=day)
