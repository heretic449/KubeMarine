#!/usr/bin/env python3

import unittest

from kubetool import demo


class TestInventoryValidation(unittest.TestCase):

    def test_labels_check(self):
        inventory = demo.generate_inventory(master=0, balancer=1, worker=0)
        inventory["nodes"][0]["labels"] = {"should": "fail"}
        with self.assertRaises(Exception) as context:
            demo.new_cluster(inventory, fake=False)

        self.assertIn("Only 'worker' or 'master' nodes can have labels", str(context.exception))

    def test_taints_check(self):
        inventory = demo.generate_inventory(master=0, balancer=1, worker=0)
        inventory["nodes"][0]["taints"] = ["should fail"]
        with self.assertRaises(Exception) as context:
            demo.new_cluster(inventory, fake=False)

        self.assertIn("Only 'worker' or 'master' nodes can have taints", str(context.exception))

    def test_invalid_node_name(self):
        inventory = demo.generate_inventory(master=1, balancer=0, worker=0)
        inventory["nodes"][0]["name"] = "bad_node/name"

        with self.assertRaises(Exception):
            demo.new_cluster(inventory, fake=False)

    def test_correct_node_name(self):
        inventory = demo.generate_inventory(master=1, balancer=0, worker=0)
        inventory["nodes"][0]["name"] = "correct-node.name123"
        demo.new_cluster(inventory, fake=False)

    def test_new_group_from_nodes(self):
        inventory = demo.generate_inventory(**demo.FULLHA_KEEPALIVED)
        cluster = demo.new_cluster(inventory)
        group = cluster.create_group_from_groups_nodes_names([], ['balancer-1', 'master-1'])
        self.assertEqual(2, len(group.nodes))

        node_names = group.get_nodes_names()
        self.assertIn('balancer-1', node_names)
        self.assertIn('master-1', node_names)

    def test_new_group_from_groups(self):
        inventory = demo.generate_inventory(**demo.FULLHA_KEEPALIVED)
        cluster = demo.new_cluster(inventory)
        group = cluster.create_group_from_groups_nodes_names(['master', 'balancer'], [])
        self.assertEqual(5, len(group.nodes))

        node_names = group.get_nodes_names()
        self.assertIn('balancer-1', node_names)
        self.assertIn('balancer-2', node_names)
        self.assertIn('master-1', node_names)
        self.assertIn('master-2', node_names)
        self.assertIn('master-3', node_names)

    def test_new_group_from_nodes_and_groups_multi(self):
        inventory = demo.generate_inventory(**demo.FULLHA_KEEPALIVED)
        cluster = demo.new_cluster(inventory)
        group = cluster.create_group_from_groups_nodes_names(['master'], ['balancer-1'])
        self.assertEqual(4, len(group.nodes))

        node_names = group.get_nodes_names()
        self.assertIn('balancer-1', node_names)
        self.assertIn('master-1', node_names)
        self.assertIn('master-2', node_names)
        self.assertIn('master-3', node_names)


if __name__ == '__main__':
    unittest.main()