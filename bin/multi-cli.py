#!/usr/local/bin/python3
import os
import sys
import json
import argparse


def run(command):
    return os.popen(command).read()


def handle(ordinals, command):

    # # Get all of the nodes for the redis cluster
    # cli_instance = json.loads(run('kubectl get pods redis-0 -o json'))
    # cli_ip = cli_instance["status"]["podIP"]
    for ordinal in ordinals:
        print(run("kubectl exec -it redis-{ordinal} -c redis -- redis-cli {command}".format(command=command, ordinal=ordinal)))


if __name__ == "__main__":
    # execute only if run as a script
    # parser = argparse.ArgumentParser(description='Scale up Redis cluster')

    # parser.add_argument('ordinals', type=str)

    # args = parser.parse_args()

    ordinals = sys.argv[1:2][0].split(",")
    command = " ".join(sys.argv[2:])
    handle(ordinals, command)
