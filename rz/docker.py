import os
import yaml
from compose.cli.command import get_project
from compose.service import build_port_bindings, build_container_ports

from rz import gce

class ComposeProject:
  def __init__(self, root, gcs_project_id, **options):
    if not root:
      raise RuntimeError("Missing root")

    self.root           = os.path.abspath(root)
    self.project        = get_project(root)
    self.gcs_project_id = gcs_project_id
    self.options        = options

  @property
  def kube_objects(self):
    return _parse_docker_compose(self.project)

  @classmethod
  def from_file(cls, gcs_project_id, filename, **kwargs):
    root = os.path.dirname(filename)
    return ComposeProject(root, gcs_project_id, **kwargs)

  def save(self, cached_path, k8config=None):
    if k8config is None:
      k8config = self.kube_objects

    with open(cached_path, 'w') as file:
      file.write(yaml.safe_dump_all(k8config,
        default_flow_style=False,
        allow_unicode=True,
        encoding='utf-8'))

  def build(self):
    for service in self.project.services:
      if 'build' in service.options:
        bucket, archive = gce.archive_codebase(self.root, 
          self.gcs_project_id, 
          self.options.get('gcs_bucket'))

        source_key = gce.upload_to_gcr(self.gcs_project_id, bucket, archive)

        image_uri = "asia.gcr.io/%s/%s" % (self.gcs_project_id, service.name)
        gce.build_from_gcr(self.gcs_project_id, bucket, source_key, image_uri)

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

    pod_spec =  {
      "spec": {
        "containers" : [{
          "name": service.name,
          "image": service.image_name,
          "ports": [{"containerPort": int(port), "protocol": "TCP" } for port in service_ports],
        }]
      },
      "metadata": {
        "name": service.name,
        "labels": {
          "app": service.name
        }
      }
    }

    if service.volumes_from:
      pod_spec['spec']['volumes'] = [
        { "name": os.path.basename(volume.external), "emptyDir": {} } for volume in service.volumes_from
      ]

    spec = pod_spec['spec']['containers'][0]

    if 'environment' in service.options:
      environment = service.options['environment']
      spec['env'] = [{ "name": str(key), "value": environment[key] } for key in environment]

    if 'command' in service.options:
      spec['args'] = service.options['command']

    if 'entrypoint' in service.options:
      spec['command'] = [service.options['entrypoint']]

    if service.options['volumes']:
      volumeMounts, volumes = [], []

      for vol in service.options['volumes']:
        vol_name = os.path.basename(vol.external).replace('_', '-')
        volumeMounts.append({"name": vol_name, "mountPath": vol.internal})

        if '/' in vol.external:
          volumes.append({ "name": vol_name, "hostPath": vol.external})
        else:
          found = False
          for _vname, _vol in project.volumes.volumes.items():
            if _vol.full_name == vol.external:
              found = True
              if _vol.driver == 'gce':
                volumes.append({ "name": vol_name, "gcePersistentDisk": _vol.driver_opts })
              if _vol.driver == 'local':
                volumes.append({ "name": vol_name, "emptyDir": {} })
              else:
                raise "Driver of type: %s is not yet supported" % _vol.driver
            
          if not found:
            volumes.append({ "name": vol_name, "emptyDir": {}})

      if volumeMounts:
        spec['volumeMounts'] = volumeMounts

      if volumes:
        pod_spec['spec']['volumes'] = volumes

    dp_spec['spec']['template'] = pod_spec

    port_bindings = build_port_bindings(service.options.get('ports', []))

    for container_port, host_ports  in port_bindings.items():
      svc_spec = {
        "kind": "Service",
        "apiVersion": "v1",
        "metadata": {
          "name": "%s-service" % service.name,
        },
        "spec": {
          "ports": [{
            "port": int(host_ports[0]),
            "targetPort": int(container_port)
          }],
          "selector": {
            "app": service.name
          },
          "type": "LoadBalancer"
        }
      }
      kube_objects.append(svc_spec)

    kube_objects.append(dp_spec)

  return kube_objects

