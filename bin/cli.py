#!/usr/local/bin/python3
import os
import sys
import json


def run(command):
    return os.popen(command).read()


def handle():

    # # Get all of the nodes for the redis cluster
    # cli_instance = json.loads(run('kubectl get pods redis-0 -o json'))
    # cli_ip = cli_instance["status"]["podIP"]

    command = " ".join(sys.argv[1:])
    print(run("kubectl exec -it redis-cli-0 -- redis-cli {command} redis-0.redis:6379".format(command=command)))


if __name__ == "__main__":
    # execute only if run as a script
    handle()
