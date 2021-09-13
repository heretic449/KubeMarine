import hashlib
import io
import random
import time

from jinja2 import Template

from kubetool import system, packages
from kubetool.core import utils
from kubetool.core.executor import RemoteExecutor
from kubetool.core.group import NodeGroup, NodeGroupResult


def autodetect_interface(cluster, name):
    for node_address, node_context in cluster.context['nodes'].items():
        if node_context['name'] == name and node_context.get('active_interface'):
            return node_context['active_interface']
        if cluster.context['initial_procedure'] == 'remove_node':
            for node_to_remove in cluster.procedure_inventory['nodes']:
                if node_to_remove['name'] == name:
                    return None
    raise Exception('Failed to autodetect active interface for %s' % name)


def enrich_inventory_apply_defaults(inventory, cluster):
    # if vrrp_ips is empty, then nothing to do
    if not inventory['vrrp_ips']:
        return inventory

    default_names = get_default_node_names(inventory)

    cluster.log.verbose("Detected default keepalived hosts: %s" % default_names)
    if not default_names:
        cluster.log.verbose("WARNING: Default keepalived hosts are empty: something can go wrong!")

    # iterate over each vrrp_ips item and check if any hosts defined to be used in it
    for i, item in enumerate(inventory['vrrp_ips']):

        if isinstance(item, str):
            inventory['vrrp_ips'][i] = item = {
                'ip': item
            }

        # is router_id defined?
        if item.get('router_id') is None:
            # is there ipv6?
            if ':' in item['ip']:
                item['router_id'] = item['ip'].split(':').pop()
                if item['router_id'] == '':
                    item['router_id'] = '0'
                # in adress with long last octet e.g. "765d" it is necessary to use only last "5d" and convert it from hex to int
                item['router_id'] = str(int(item['router_id'][-2:], 16))
            else:
                item['router_id'] = item['ip'].split('.').pop()

        # is id defined?
        if item.get('id') is None:
            # label max size is 15, then 15 - 5 (size from string "vip_") = 10 symbols we can use
            source_string = item.get('interface', 'auto') + item['ip']
            label_size = cluster.globals['keepalived']['defaults']['label_size']
            item['id'] = hashlib.md5(source_string.encode('utf-8')).hexdigest()[:label_size]

        # is password defined?
        if item.get('password') is None:
            password_size = cluster.globals['keepalived']['defaults']['password_size']
            item['password'] = ("%032x" % random.getrandbits(128))[:password_size]

        # if nothing defined then use default names
        if item.get('hosts') is None:
            # is there default names found?
            if not default_names:
                raise Exception('Section #%s in vrrp_ips has no hosts, but default names can\'t be found.' % i)
            # ok, default names found, and can be used
            inventory['vrrp_ips'][i]['hosts'] = default_names

        for j, record in enumerate(item['hosts']):
            if isinstance(record, str):
                item['hosts'][j] = {
                    'name': record
                }
            if not item['hosts'][j].get('priority'):
                item['hosts'][j]['priority'] = cluster.globals['keepalived']['defaults']['priority']['max_value'] - \
                                               (j + cluster.globals['keepalived']['defaults']['priority']['step'])
            if not item['hosts'][j].get('interface') and item.get('interface'):
                item['hosts'][j]['interface'] = item['interface']
            if item['hosts'][j].get('interface', 'auto') == 'auto':
                item['hosts'][j]['interface'] = autodetect_interface(cluster, item['hosts'][j]['name'])

    return inventory


def get_default_node_names(inventory):
    default_names = []

    # well, vrrp_ips is not empty, let's find balancers defined in config-file
    for i, node in enumerate(inventory.get('nodes', [])):
        if 'balancer' in node['roles'] and \
                (node.get('address') is not None or node.get('internal_address') is not None):
            default_names.append(node['name'])

    # just in case, we remove duplicates
    return list(set(default_names))


def enrich_inventory_calculate_nodegroup(inventory, cluster):
    # if vrrp_ips is empty, then nothing to do
    if not inventory['vrrp_ips']:
        return inventory

    # Calculate group, where keepalived should be installed:
    names = []

    for i, item in enumerate(cluster.inventory['vrrp_ips']):
        for record in item['hosts']:
            names.append(record['name'])

    # it is important to remove duplicates
    names = list(set(names))

    filtered_members = cluster.nodes['all'].get_ordered_members_list(provide_node_configs=False, apply_filter={
        'name': names
    })

    # create new group where keepalived will be installed
    cluster.nodes['keepalived'] = cluster.make_group(filtered_members)

    # create new role
    cluster.roles.append('keepalived')

    # fill in ips
    cluster.ips['keepalived'] = list(cluster.nodes['keepalived'].nodes.keys())

    return inventory


def install(group):
    log = group.cluster.log

    package_associations = group.cluster.inventory['services']['packages']['associations']['keepalived']

    keepalived_version = group.sudo("%s -v" % package_associations['executable_name'], warn=True)
    keepalived_installed = True

    for connection, result in keepalived_version.items():
        if result.exited != 0:
            keepalived_installed = False

    if keepalived_installed:
        log.debug("Keepalived already installed, nothing to install")
        installation_result = keepalived_version
    else:
        installation_result = packages.install(group.get_new_nodes_or_self(), include=package_associations['package_name'])

    service_name = group.cluster.inventory['services']['packages']['associations']['keepalived']['service_name']
    patch_path = utils.get_resource_absolute_path("./resources/drop_ins/keepalived.conf", script_relative=True)
    group.call(system.patch_systemd_service, service_name=service_name, patch_source=patch_path)
    group.call(install_haproxy_check_script)
    enable(group)

    return installation_result


def install_haproxy_check_script(group: NodeGroup):
    local_path = utils.get_resource_absolute_path("./resources/scripts/check_haproxy.sh", script_relative=True)
    group.put(local_path, "/usr/local/bin/check_haproxy.sh", sudo=True, binary=False)
    group.sudo("chmod +x /usr/local/bin/check_haproxy.sh")


def uninstall(group):
    return packages.remove(group, include='keepalived')


def restart(group):
    results = NodeGroupResult()
    for node in group.get_ordered_members_list(provide_node_configs=True):
        os_specific_associations = group.cluster.get_associations_for_node(node['connect_to'])
        package_associations = os_specific_associations['keepalived']
        results.update(system.restart_service(node['connection'], name=package_associations['service_name']))
        group.cluster.log.debug("Sleep while keepalived comes-up...")
        time.sleep(group.cluster.globals['keepalived']['restart_wait'])
    return results


def enable(group):
    with RemoteExecutor(group.cluster.log):
        for node in group.get_ordered_members_list(provide_node_configs=True):
            os_specific_associations = group.cluster.get_associations_for_node(node['connect_to'])
            system.enable_service(node['connection'], name=os_specific_associations['keepalived']['service_name'],
                                  now=True)


def disable(group):
    with RemoteExecutor(group.cluster.log):
        for node in group.get_ordered_members_list(provide_node_configs=True):
            os_specific_associations = group.cluster.get_associations_for_node(node['connect_to'])
            system.disable_service(node['connection'], name=os_specific_associations['keepalived']['service_name'])


def generate_config(inventory, node):
    config = ''

    for i, item in enumerate(inventory['vrrp_ips']):

        if i > 0:
            # this is required for double newline in config, but avoid double newline in the end of file
            config += "\n"

        ips = {
            'source': node['internal_address'],
            'peers': []
        }

        priority = 100
        interface = 'eth0'
        for record in item['hosts']:
            if record['name'] == node['name']:
                priority = record['priority']
                interface = record['interface']

        for i_node in inventory['nodes']:
            for record in item['hosts']:
                if i_node['name'] == record['name'] and i_node['internal_address'] != ips['source']:
                    ips['peers'].append(i_node['internal_address'])

        template_location = utils.get_resource_absolute_path('templates/keepalived.conf.j2', script_relative=True)
        config += Template(open(template_location).read()).render(inventory=inventory, item=item, node=node,
                                                                  interface=interface,
                                                                  priority=priority, **ips) + "\n"

    return config


def configure(group):
    log = group.cluster.log
    group_members = group.get_ordered_members_list(provide_node_configs=True)

    with RemoteExecutor(log):
        for node in group_members:

            log.debug("Configuring keepalived on '%s'..." % node['name'])

            package_associations = group.cluster.get_associations_for_node(node['connect_to'])['keepalived']
            configs_directory = '/'.join(package_associations['config_location'].split('/')[:-1])

            group.sudo('mkdir -p %s' % configs_directory, hide=True)

            config = generate_config(group.cluster.inventory, node)
            utils.dump_file(group.cluster, config, 'keepalived_%s.conf' % node['name'])

            node['connection'].put(io.StringIO(config), package_associations['config_location'], sudo=True)

    log.debug(group.sudo('ls -la %s' % package_associations['config_location']))

    log.debug("Restarting keepalived in all group...")
    restart(group)

    return group.sudo('systemctl status %s' % package_associations['service_name'], warn=True)