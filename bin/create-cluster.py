#!/usr/local/bin/python3
import argparse
import os
import json
import yaml
import time


def run(command):
    return os.popen(command).read()


def handle():

    # Get all of the nodes for the redis cluster
    instances = json.loads(run('kubectl get pods -l app=redis -o json'))

    # Get all of the IPs for the nodes which need to in the Redis cluster
    node_ips = list(map(lambda instance: {'ip': instance["status"]["podIP"]}, instances.get('items')))

    # Run Redis cluster create with IPs
    print(run("kubectl exec -it redis-cli-0 -- redis-cli --cluster create --cluster-yes {nodes}".format(nodes=" ".join(["{}:6379".format(ip["ip"]) for ip in node_ips]))))


if __name__ == "__main__":
    # execute only if run as a script
    handle()
