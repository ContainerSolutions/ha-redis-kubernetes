#!/usr/local/bin/python3
import argparse
import os
import json
import yaml
import time
import pdb


def run(command):
    return os.popen(command).read()

def get_redis_ip():
    original_redis = json.loads(run('kubectl get pods redis-0 -o json'))
    return original_redis["status"]["podIP"]

def get_live_config():
    return json.loads(run('kubectl get statefulset redis -o json'))

def read_config():
    with open("./redis/statefulset.yml", "r") as yaml_file:
        statefulset = yaml.safe_load(yaml_file)
        return statefulset


def update_config(statefulset):
    with open('./redis/statefulset.yml', 'w') as outfile:
        yaml.dump(statefulset, outfile, default_flow_style=False)


def move_slots_away_from_imminent_departure_nodes(current_replicas, new_replicas):
    if new_replicas >= current_replicas:
        print("Not moving slots, as cluster is not scaling down")
        return 

    amount = current_replicas - new_replicas

    current_statefulset = get_live_config()
    comms_ip = get_redis_ip()

    removing_ordinals = list(range(current_replicas-amount, current_replicas))

    print("Building node id list for removal")
    node_ids_to_remove = []
    for ordinal in removing_ordinals:
        node_id = run("kubectl exec -it redis-{ordinal} -c redis -- redis-cli -p 6379 CLUSTER MYID".format(ordinal=ordinal)).strip()[1:-1]
        node_ids_to_remove.append(node_id)
    print("Node id list for removal", node_ids_to_remove)

    print("Rebalancing away from removal nodes")
    rebalance(away_from_nodes=node_ids_to_remove)
    print("Done Rebalancing")

    print("Deleting nodes ")
    for id in node_ids_to_remove:
        print(run("kubectl exec -it redis-cli-0 -- redis-cli --cluster del-node {redis_host}:6379 {node_id}".format(redis_host=comms_ip, node_id=id)))
        print("{} Deleted".format(id))
        time.sleep(1)
    print("Done deleting")

    print("Marking PVCs for deletion")
    for ordinal in removing_ordinals:
        run("kubectl delete pvc/redis-data-redis-{ordinal} --wait=false".format(ordinal=ordinal))
    print("Done marking PVCs for deletion")


def add_new_nodes(previous_replicas, current_replicas):
    if current_replicas <= previous_replicas:
        print("Not adding new nodes, as cluster is not scaling up")
        return 

    current_statefulset = get_live_config()
    amount = current_replicas - previous_replicas
    starting_pod_ip = get_redis_ip()

    print("Starting to add new nodes to cluster")
    for new_ordinal in range(previous_replicas, current_replicas):
        print("New ordinal", new_ordinal)
        new_ordinal_pod = json.loads(run('kubectl get pods redis-{ordinal} -o json'.format(ordinal=new_ordinal)))
        print(run("kubectl exec -it redis-cli-0 -- redis-cli --cluster add-node {new_host}:6379 {host}:6379".format(new_host=new_ordinal_pod["status"]["podIP"], host=starting_pod_ip)))
        time.sleep(1)
    print("Done adding new nodes to cluster")


def rebalance_whole_cluster():
    run("kubectl exec -it redis-cli-0 -- redis-cli --cluster rebalance redis-0.redis:6379 --cluster-use-empty-masters")

def rebalance(away_from_nodes):
    print(run("kubectl exec -it redis-cli-0 -- redis-cli --cluster rebalance redis-0.redis:6379 --cluster-use-empty-masters --cluster-weight {cluster_weights}".format(
        cluster_weights=" ".join(["{}=0".format(id) for id in away_from_nodes])
    )))


def propogate_config():
    print(run("kubectl apply -k ./redis --wait"))
    print(run("kubectl rollout status statefulset/redis"))


def handle():

    original_config = json.loads(run('kubectl get statefulset redis -o json'))
    updated_config = read_config()

    """
    First thing we need to do is update 
    the rollout partition for the statefulset
    So our update will not immediately propogate 
    to all of the nodes
    and we can slowly rollout our upgrade
    """

    # We need to get the current replicas so we can set the partion to it.
    # The ordinal is zero indexed, so we do not need to do replicas + 1
    current_replicas = original_config["spec"]["replicas"]
    current_partition = current_replicas

    # Next we need to update the rollout strategy with the partition count
    updated_config["spec"]["updateStrategy"] = {
        "type": "RollingUpdate",
        "rollingUpdate": {
            "partition": current_partition
        }
    }
    update_config(updated_config)

    # Next we need to make sure to remove slot from nodes who are imminent to departure,
    # in case we are scaling down the cluster
    move_slots_away_from_imminent_departure_nodes(current_replicas, updated_config["spec"]["replicas"])

    # We can now update the statefulset.
    # If replicas have been increased, the new pods will be deployed with the updates, 
    # But the already running cluster nodes, will remain the same until we decrease the partition.
    propogate_config()

    # If the cluster has been increased, we need to add the new nodes, 
    # and rebalance the cluster so they have some slots
    add_new_nodes(current_replicas, updated_config["spec"]["replicas"])
    rebalance_whole_cluster()


    ## Check whether there are changes which constitute a full upgrade procedure
    should_upgrade = False

    ## If there are changes to the images, we should run upgrade
    original_images = [container["image"] for container in original_config["spec"]["template"]["spec"]["containers"]]
    updated_images = [container["image"] for container in updated_config["spec"]["template"]["spec"]["containers"]]
    if (original_images != updated_images):
        should_upgrade = True

    if not should_upgrade:
        print("Skipping cluster upgrade, as no changes constitue full upgrade")

    if should_upgrade:
        print("Starting Cluster Upgrade")

        # For the partition, we need to use the lower replicas from the previous,
        # and from the current replicas.

        # Reasoning:
        # If we scaled down from 7 to 3,
        # we need to use 3 as 7 is no longer available.
        # Outcome: Use new replicas

        # If we scaled up from 3 to 7
        # we already set the partition to 3 earlier and all the new pods have already got the new update.
        # So only the first three still need to be upgraded, as the new ones already are.
        # Therefor we need to use the 3 again. 
        # Outcome: Use old replicas

        # The common denominator is the lower of the two. Therefor we use that

        partition_end = min(updated_config["spec"]["replicas"], original_config["spec"]["replicas"])

        for partition in reversed(range(partition_end)):
            """
            Now that we have the full cluster up, 
            we can move on to actually updating the existing nodes which have data.
            The process for this, to be highly available, will be to move slots away from 
            the ordinal which we are currently at, then lower the partition to trigger Kubernetes
            to update it
            """
            print("############")
            print("# Starting upgrade for partition", partition)
            print("############")

            # First we need to find the node id to remove it from the rebalancing
            print("Rebalancing away from upgradeable node")
            node_id = run("kubectl exec -it redis-{ordinal} -c redis -- redis-cli -p 6379 CLUSTER MYID".format(ordinal=partition)).strip()[1:-1]

            # We need to rebalance the cluster, and set the weight for our current node to 0.
            # This will move all of it's slot accross the other node,
            # and end in a state of equal distribution across the other nodes
            rebalance(away_from_nodes=[node_id])
            print("Done rebalancing away from upgradeable node")

            print("Updating partition to", partition)
            updated_config["spec"]["updateStrategy"]["rollingUpdate"] = {
                "partition": partition
            }
            update_config(updated_config)
            print("Done updating partition to", partition)

            print("Propogating partition and waiting for update to finish")
            propogate_config()
            print("Done propogating partition and waiting for update to finish")

            print("Waiting for 20 seconds before moving onto the next partition")
            time.sleep(20)
            print("Done waiting")

            print("############")
            print("# Finished upgrade for partition", partition)
            print("############")
            


    # Do a final rebalance to set the cluster in a good state
    rebalance_whole_cluster()


if __name__ == "__main__":
    # execute only if run as a script
    handle()

"""


















"""
