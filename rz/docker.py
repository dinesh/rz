import os
import re
import sys
import yaml
import subprocess
from compose.cli.command import get_project
from compose.service import build_port_bindings, build_container_ports
from rz import gce


class ComposeProject:

    def __init__(self, root):
        if root is None:
            raise RuntimeError("Missing root")

        self.root = os.path.abspath(root)
        self.project = get_project(root)

    def kube_objects(self, **kwargs):
        return _parse_docker_compose(self.project, **kwargs)

    def save_for_k8(self, path, objects=None):
        if objects is None:
            objects = self.kube_objects()

        with open(path, 'w') as file:
            file.write(yaml.safe_dump_all(objects,
                                          default_flow_style=False,
                                          allow_unicode=True,
                                          encoding='utf-8'))

    def build_with(self, builder, config, skip=False):
        for service in self.project.services:
            if not skip:
                if 'build' in service.options:
                    if builder == 'google':
                        project_id, image_uri = config[
                            'project_id'], service.name
                        bucket = config.get(
                            'bucket_name',
                            "{}-cbstorage".format(config['project_id'])
                        )

                        gcr_hostname = get_gcr_hostname(config['zone'])
                        image_uri = "%s/%s/%s" % (gcr_hostname,
                                                  config['project_id'],
                                                  service.name)
                        bucket, archive = gce.archive_codebase(
                            self.root, project_id, bucket)

                        source_key = gce.upload_to_gcr(
                            project_id, bucket, archive)
                        gce.build_from_gcr(
                            project_id, bucket, source_key,
                            image_uri, service.options['build'])

                    elif builder == 'local':
                        image_uri, options = service.name, service.options[
                            'build'].copy()
                        options['tag'] = image_uri
                        build_with_docker(options)
                    else:
                        raise ValueError("unknown builder: %s" % builder)

                    service.options.pop('build')
                    service.options['image'] = image_uri

                elif 'image' in service.options:
                    if builder == 'local':
                        build_with_docker({'pull': service.options['image']})
                else:
                    raise ValueError(
                        "no image or build value found for service: %s" %
                        service.name)


def build_with_docker(req, pull_image=True, dry_run=False):
    errmsg = None
    try:
        subprocess.check_output(["docker", "version"])
    except subprocess.CalledProcessError, e:
        errmsg = "Cannot communicate with docker daemon: " + str(e)
    except OSError as e:
        errmsg = "'docker' executable not found: " + str(e)

    if errmsg:
        raise RuntimeError(errmsg)

    return get_image(req, pull_image, dry_run)


def get_image(req, pull_image, dry_run=False):
    found = False

    assert req

    if 'image' not in req and 'pull' in req:
        req['image'] = req['pull']

        for ln in subprocess.check_output(["docker", "images", "--no-trunc", "--all"]).splitlines():
            try:
                m = re.match(r"^([^ ]+)\s+([^ ]+)\s+([^ ]+)", ln)
                sp = req["image"].split(":")
                if len(sp) == 1:
                    sp.append("latest")
                if ((sp[0] == m.group(1) and sp[1] == m.group(2)) or req["image"] == m.group(3)):
                    found = True
                    break
            except ValueError:
                pass

    if not found and pull_image:
        if 'image' in req:
            cmd = ["docker", "pull", req["image"]]
            print str(cmd)

            if not dry_run:
                subprocess.check_call(cmd, stdout=sys.stderr)
                found = True
        elif "context" in req:
            req['dockerfile'] = req.get('dockerfile', 'Dockerfile')
            cmd = ["docker", "build", "--tag=%s" %
                   req["tag"], "-f", req['dockerfile'], req['context']]
            print str(cmd)

            if not dry_run:
                subprocess.check_call(cmd, stdout=sys.stderr)
                found = True

    return found


def _parse_docker_compose(project, **kwargs):
    ns_name, kube_objects = kwargs.get('namespace', 'default'), []
    dp_version = 'extensions/v1beta1'

    if ns_name != 'default':
        ns_spec = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": ns_name
            }
        }
        kube_objects.append(ns_spec)

    for service in project.services:
        service_ports = build_container_ports(service.options, service.options)

        dp_spec = {
            "kind": "Deployment",
            "apiVersion": dp_version,
            "metadata": {
                "name": service.name,
                "namespace": ns_name
            },
            "spec": {
                "replicas": 1,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {
                        "maxUnavailable": 0,
                        "maxSurge": 1
                    }
                },
                "template": None
            }
        }

        exposed_ports = []
        for port in service_ports:
            if isinstance(port, basestring):
                exposed_ports.append(
                    {"containerPort": int(port), "protocol": "TCP"})
            elif isinstance(port, (tuple, list)):
                exposed_ports.append(
                    {"containerPort": int(port[0]),
                     "protocol": port[1].upper()})
            else:
                raise ValueError("Unexpected value for port: %s" % port)

        pod_spec = {
            "spec": {
                "containers": [{
                    "name": service.name,
                    "image": service.image_name,
                    "ports": exposed_ports,
                    "imagePullPolicy": "Never"
                }]
            },
            "metadata": {
                "name": service.name,
                "namespace": ns_name,
                "labels": {
                    "app": service.name
                }
            }
        }

        service_volumes = service.options.get('volumes', [])[:]

        if service.volumes_from:
            for external_volume in service.volumes_from:
                for vol_spec in external_volume.source.options['volumes']:
                    service_volumes.append(vol_spec)

        spec = pod_spec['spec']['containers'][0]

        restart_policy = _get_restart_policy(service.options.get('restart'))
        if restart_policy:
            spec['restartPolicy'] = restart_policy

        if 'environment' in service.options:
            environment = service.options['environment']
            spec['env'] = [
                {"name": str(key), "value": environment[key]}
                for key in environment
            ]

        if 'command' in service.options:
            spec['args'] = service.options['command']

        if 'entrypoint' in service.options:
            spec['command'] = [service.options['entrypoint']]

        if service_volumes:
            volumeMounts, volumes = [], []

            for vol in service_volumes:
                vol_name = os.path.basename(vol.external).replace('_', '-')
                volumeMounts.append(
                    {"name": vol_name, "mountPath": vol.internal})

                if '/' in vol.external:
                    volumes.append(
                        {"name": vol_name, "hostPath": {"path": vol.external}})
                else:
                    found = False
                    for _vname, _vol in project.volumes.volumes.items():
                        if _vol.full_name == vol.external:
                            found = True
                            if _vol.driver == 'gce':
                                volumes.append({
                                    "name": vol_name,
                                    "gcePersistentDisk": _vol.driver_opts
                                })
                            if _vol.driver == 'local' or _vol.driver is None:
                                volumes.append(
                                    {"name": vol_name, "emptyDir": {}})
                            else:
                                raise RuntimeError(
                                    "Driver of type: %s is not yet supported" %
                                    _vol.driver)

                    if not found:
                        volumes.append({"name": vol_name, "emptyDir": {}})

            if volumeMounts:
                spec['volumeMounts'] = volumeMounts

            if volumes:
                pod_spec['spec']['volumes'] = volumes

        dp_spec['spec']['template'] = pod_spec

        port_bindings = build_port_bindings(service.options.get('ports', []))

        for container_port, host_ports in port_bindings.items():
            _port = container_port.split('/')[0]
            _host_port = host_ports[0] or _port

            svc_spec = {
                "kind": "Service",
                "apiVersion": "v1",
                "metadata": {
                    "name": service.name,
                    "namespace": ns_name
                },
                "spec": {
                    "ports": [{
                        "port": int(_host_port),
                        "targetPort": int(_port),
                    }],
                    "selector": {
                        "app": service.name
                    }
                }
            }

            if int(_host_port) == 80:
                svc_spec['spec']['type'] = "LoadBalancer"

            kube_objects.append(svc_spec)

        kube_objects.append(dp_spec)

        ignored_fields = ['cap_add', 'cap_drop', 'cgroup_parent', 'container_name', 
            'devices', 'depends_on', 'dns', 'dns_search', 'tmpfs', 'extends', 
            'external_links', 'extra_hosts', 'labels', 'links', 'logging']

        for field in ignored_fields:
            if field in service.options:
                print "warning: skipping: %s\n" % field

    return kube_objects


def _get_restart_policy(options):
    if options:
        name = options['Name']
        if name == 'always':
            return 'Always'
        elif name == 'no':
            return "Never"
        elif name == 'on-failure':
            return 'OnFailure'


def get_gcr_hostname(zone):
    prefix = None
    if re.match('asia-', zone):
        prefix = 'asia'
    elif re.match('us-', zone):
        prefix = 'us'
    elif re.match('europe', zone):
        prefix = 'eu'

    return "{}.gcr.io".format(prefix) if prefix else 'gcr.io'
