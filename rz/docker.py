import os
import yaml
from compose.cli.command import get_project
from compose.service import build_port_bindings, build_container_ports

from rz import gce

class ComposeProject:
  def __init__(self, root, gce_project_id, **options):
    if root is None:
      raise RuntimeError("Missing root")

    self.root           = os.path.abspath(root)
    self.project        = get_project(root)
    self.gce_project_id = gce_project_id
    self.options        = options

  @property
  def kube_objects(self):
    return _parse_docker_compose(self.project)

  @classmethod
  def from_file(cls, gce_project_id, project_path, **kwargs):
    return ComposeProject(project_path, gce_project_id, **kwargs)

  def save(self, cached_path, k8config=None):
    if k8config is None:
      k8config = self.kube_objects

    with open(cached_path, 'w') as file:
      file.write(yaml.safe_dump_all(k8config,
        default_flow_style=False,
        allow_unicode=True,
        encoding='utf-8'))

  def build(self, skip=False):
    for service in self.project.services:
      if 'build' in service.options:
        bucket = self.options.get('gce_bucket', '%s-cbstorage' % self.gce_project_id)
        image_uri = "asia.gcr.io/%s/%s" % (self.gce_project_id, service.name)
        
        if not skip:
          bucket, archive = gce.archive_codebase(self.root, 
                              self.gce_project_id, bucket)

          source_key = gce.upload_to_gcr(self.gce_project_id, bucket, archive)
          gce.build_from_gcr(self.gce_project_id, bucket, source_key, image_uri)

        service.options.pop('build')
        service.options['image'] = image_uri

def _parse_docker_compose(project):
  kube_objects = []
  dp_version = 'extensions/v1beta1'

  for service in project.services:
    service_ports = build_container_ports(service.options, service.options)

    dp_spec = {
      "kind": "Deployment",
      "apiVersion": dp_version,
      "metadata": {
        "name": service.name,
      },
      "spec": {
        "replicas": 1,
        "template": None
      }
    }

    exposed_ports = []
    for port in service_ports:
      if isinstance(port, basestring):
        exposed_ports.append({"containerPort": int(port), "protocol": "TCP"})
      elif isinstance(port, (tuple, list)):
        exposed_ports.append({"containerPort": int(port[0]), "protocol": port[1].upper() })
      else:
        raise ValueError("Unexpected value for port: %s" % port)

    pod_spec =  {
      "spec": {
        "containers" : [{
          "name": service.name,
          "image": service.image_name,
          "ports": exposed_ports,
        }]
      },
      "metadata": {
        "name": service.name,
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

    if 'environment' in service.options:
      environment = service.options['environment']
      spec['env'] = [{"name": str(key), "value": environment[key] } for key in environment]

    if 'command' in service.options:
      spec['args'] = service.options['command']

    if 'entrypoint' in service.options:
      spec['command'] = [service.options['entrypoint']]

    if service_volumes:
      volumeMounts, volumes = [], []

      for vol in service_volumes:
        vol_name = os.path.basename(vol.external).replace('_', '-')
        volumeMounts.append({"name": vol_name, "mountPath": vol.internal})

        if '/' in vol.external:
          volumes.append({ "name": vol_name, "hostPath": { "path": vol.external}})
        else:
          found = False
          for _vname, _vol in project.volumes.volumes.items():
            if _vol.full_name == vol.external:
              found = True
              if _vol.driver == 'gce':
                volumes.append({ "name": vol_name, "gcePersistentDisk": _vol.driver_opts })
              if _vol.driver == 'local' or _vol.driver is None:
                volumes.append({ "name": vol_name, "emptyDir": {} })
              else:
                raise RuntimeError("Driver of type: %s is not yet supported" % _vol.driver)
            
          if not found:
            volumes.append({ "name": vol_name, "emptyDir": {}})

      if volumeMounts:
        spec['volumeMounts'] = volumeMounts

      if volumes:
        pod_spec['spec']['volumes'] = volumes

    dp_spec['spec']['template'] = pod_spec

    port_bindings = build_port_bindings(service.options.get('ports', []))

    for container_port, host_ports  in port_bindings.items():
      _port = container_port.split('/')[0]
      _host_port = host_ports[0] or _port

      svc_spec = {
        "kind": "Service",
        "apiVersion": "v1",
        "metadata": {
          "name": service.name,
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

  return kube_objects

