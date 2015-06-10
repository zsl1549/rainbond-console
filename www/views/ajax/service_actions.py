# -*- coding: utf8 -*-
import datetime
import json

from django.db.models import Sum
from django.views.decorators.cache import never_cache
from django.http import JsonResponse
from www.views import AuthedView
from www.decorator import perm_required

from www.models import TenantServiceInfo, TenantServiceLog, PermRelService, TenantServiceRelation, TenantServiceStatics
from www.service_http import RegionServiceApi
from www.weblog import WebLog
from www.gitlab_http import GitlabApi
from goodrain_web.tools import BeanStalkClient
from www.etcd_client import EtcdClient
from django.conf import settings
from www.db import BaseConnection


import logging
from django.template.defaultfilters import length
logger = logging.getLogger('default')

weblog = WebLog()

gitClient = GitlabApi()

beanlog = BeanStalkClient()

class AppDeploy(AuthedView):
    @perm_required('code_deploy')
    def post(self, request, *args, **kwargs):
        service_alias = ""
        data = {}
        tenant_id = self.tenant.tenant_id
        service_id = self.service.service_id
        
        try:
            task = {}
            task["log_msg"] = "开始部署......"
            task["service_id"] = service_id
            task["tenant_id"] = tenant_id
            logger.info(task)                
            beanlog.put("app_log", json.dumps(task))
        except Exception as e:
            logger.exception(e)

        try:
            gitUrl = request.POST.get('git_url', None)
            if gitUrl is None:
                gitUrl = self.service.git_url

            service_alias = self.service.service_alias

            body = {}
            if(self.service.deploy_version == ""):
                body["action"] = "deploy"
            else:
                body["action"] = "upgrade"

            self.service.deploy_version = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
            self.service.save()

            body["deploy_version"] = self.service.deploy_version
            body["gitUrl"] = gitUrl

            para = json.dumps(body)
            client = RegionServiceApi()
            client.build_service(service_id, para)

            log = {
                "user_id": self.user.pk, "user_name": self.user.nick_name,
                "service_id": service_id, "tenant_id": tenant_id,
                "action": "deploy",
            }

            TenantServiceLog.objects.create(**log)

            data["status"] = "success"
            return JsonResponse(data, status=200)
        except Exception as e:
            weblog.info(self.tenantName, service_alias, "%s" % e)
            logger.info("%s" % e)
            data["status"] = "failure"
        return JsonResponse(data, status=500)


class AllServiceInfo(AuthedView):
    @perm_required('tenant.tenant_access')
    def get(self, request, *args, **kwargs):
        result = {}
        service_ids = []
        try:
            service_list = TenantServiceInfo.objects.filter(tenant_id=self.tenant.tenant_id).values('ID', 'service_id', 'deploy_version')
            if self.has_perm('tenant.list_all_services'):
                # service_ids = [e['service_id'] for e in service_list]
                for s in service_list:
                    if s['deploy_version'] is None or s['deploy_version'] == "":
                        child1 = {}
                        child1["totalMemory"] = 0
                        child1["status"] = "Undeployed"
                        result[s['service_id']] = child1
                    else:
                        service_ids.append(s['service_id'])                            
            else:
                service_pk_list = PermRelService.objects.filter(user_id=self.user.pk).values_list('service_id', flat=True)
                for s in service_list:
                    if s['ID'] in service_pk_list:
                            if s['deploy_version'] is None or s['deploy_version'] == "":
                                child1 = {}
                                child1["totalMemory"] = 0
                                child1["status"] = "Undeployed"
                                result[s.service_id] = child1
                            else:
                                service_ids.append(s['service_id'])
            id_string = ','.join(service_ids)
            client = RegionServiceApi()
            bodys = client.check_status(json.dumps({"service_ids": id_string}))

            for sid in service_ids:
                service = TenantServiceInfo.objects.get(service_id=sid)
                body = bodys[sid]
                nodeNum = 0
                runningNum = 0
                isDeploy = 0
                child = {}
                for item in body:
                    nodeNum += 1
                    status = body[item]['status']
                    if status == "Undeployed":
                        isDeploy = -1
                        break                      
                    elif status == 'Running':
                        runningNum += 1
                        isDeploy += 1
                    else:
                        isDeploy += 1
                if isDeploy > 0:
                    if nodeNum == runningNum:
                        if runningNum > 0:
                            child["totalMemory"] = runningNum * service.min_memory
                            child["status"] = "Running"
                        else:
                            child["totalMemory"] = 0
                            child["status"] = "Waiting"
                    else:
                        child["totalMemory"] = 0
                        child["status"] = "Waiting"
                elif isDeploy == -1 :
                    child["totalMemory"] = 0
                    child["status"] = "Undeployed"
                else:
                    child["totalMemory"] = 0
                    child["status"] = "Closing"
                result[sid] = child
        except Exception, e:
            logger.exception(e)
            logger.info("%s" % e)
            for sid in service_ids:
                child = {}
                child["totalMemory"] = 0
                child["status"] = "failure"
                result[sid] = child
        return JsonResponse(result)


class ServiceManage(AuthedView):
    @perm_required('manage_service')
    def post(self, request, *args, **kwargs):
        result = {}
        action = request.POST["action"]
        try:
            client = RegionServiceApi()
            if action == "stop":
                client.stop(self.service.service_id)
                
                task = {}
                task["log_msg"] = "服务已关闭"
                task["service_id"] = self.service.service_id
                task["tenant_id"] = self.tenant.tenant_id
                # logger.info(task)                
                beanlog.put("app_log", json.dumps(task))
            elif action == "restart":                
                client.restart(self.service.service_id)
                
                task = {}
                task["log_msg"] = "服务已启动"
                task["service_id"] = self.service.service_id
                task["tenant_id"] = self.tenant.tenant_id
                # logger.info(task)                
                beanlog.put("app_log", json.dumps(task))  
            elif action == "delete":
               client.delete(self.service.service_id)
            result["status"] = "success"
        except Exception, e:
            logger.info("%s" % e)
            result["status"] = "failure"
        return JsonResponse(result)


class ServiceUpgrade(AuthedView):
    @perm_required('manage_service')
    def post(self, request, *args, **kwargs):
        result = {}
        action = request.POST["action"]        
        client = RegionServiceApi()
        if action == "vertical":
            try:
                container_memory = request.POST["memory"]
                container_cpu = request.POST["cpu"]
                old_container_cpu=self.service.min_cpu
                old_container_memory=self.service.min_memory
                old_deploy_version=self.service.deploy_version
                if int(container_memory) > 0  and int(container_cpu) > 0: 
                    self.service.min_cpu = container_cpu          
                    self.service.min_memory = container_memory
                    self.service.deploy_version = deploy_version
                    self.service.save()
                    
                    dsn = BaseConnection()
                    query_sql = '''
                        select sum(s.min_node * s.min_memory) as totalMemory from tenant_service s where s.tenant_id = "{tenant_id}"
                        '''.format(tenant_id=self.tenant.tenant_id)
                    logger.debug(query_sql)
                    sqlobj = dsn.query(query_sql)
                    totalMemory = sqlobj["totalMemory"]
                    logger.debug(totalMemory)
                    if int(totalMemory) > 1024:
                        self.service.min_node = old_min_node
                        self.service.deploy_version = old_deploy_version
                        self.service.save()
                        result["status"] = "overtop"
                        return JsonResponse(result)
                    
                    task = {}
                    task["log_msg"] = "服务开始垂直扩容部署"
                    task["service_id"] = self.service.service_id
                    task["tenant_id"] = self.tenant.tenant_id
                    beanlog.put("app_log", json.dumps(task))
                    
                    deploy_version = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                    body = {}
                    body["container_memory"] = container_memory
                    body["deploy_version"] = deploy_version
                    body["container_cpu"] = container_cpu
                    client.verticalUpgrade(self.service.service_id, json.dumps(body)) 
                result["status"] = "success"                       
            except Exception, e:
                self.service.min_cpu = old_container_cpu          
                self.service.min_memory = old_container_memory
                self.service.deploy_version = old_deploy_version
                self.service.save() 
                logger.info("%s" % e)
                result["status"] = "failure"            
        elif action == "horizontal":
            node_num = request.POST["node_num"]
            old_min_node = self.service.min_node
            old_deploy_version = self.service.deploy_version
            try:
                if int(node_num) >= 0:                    
                    self.service.min_node = node_num
                    self.service.deploy_version = deploy_version
                    self.service.save()
                                        
                    dsn = BaseConnection()
                    query_sql = '''
                        select sum(s.min_node * s.min_memory) as totalMemory from tenant_service s where s.tenant_id = "{tenant_id}"
                        '''.format(tenant_id=self.tenant.tenant_id)
                    logger.debug(query_sql)
                    sqlobj = dsn.query(query_sql)
                    totalMemory = sqlobj["totalMemory"]
                    logger.debug(totalMemory)                    
                    if int(totalMemory) > 1024:
                        self.service.min_node = old_min_node
                        self.service.deploy_version = old_deploy_version
                        self.service.save()
                        result["status"] = "overtop"
                        return JsonResponse(result)
                    
                    task = {}
                    task["log_msg"] = "服务开始水平扩容部署"
                    task["service_id"] = self.service.service_id
                    task["tenant_id"] = self.tenant.tenant_id
                    beanlog.put("app_log", json.dumps(task))         
                    
                    deploy_version = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                    body = {}
                    body["node_num"] = node_num   
                    body["deploy_version"] = deploy_version
                    client.horizontalUpgrade(self.service.service_id, json.dumps(body))
                result["status"] = "success"
            except Exception, e:
                self.service.min_node = old_min_node
                self.service.deploy_version = old_deploy_version
                self.service.save()
                logger.info("%s" % e)
                result["status"] = "failure"            
        return JsonResponse(result)

class ServiceRelation(AuthedView):
    @perm_required('manage_service')
    def post(self, request, *args, **kwargs):
        result = {}
        action = request.POST["action"]
        dep_service_alias = request.POST["dep_service_alias"]
        try:
            tenant_id = self.tenant.tenant_id
            service_id = self.service.service_id
            tenantS = TenantServiceInfo.objects.get(tenant_id=tenant_id, service_alias=dep_service_alias)
            etcdPath = '/goodrain/' + tenant_id + '/services/' + service_id + '/dependency/' + tenantS.service_id
            etcdClient = EtcdClient(settings.ETCD.get('host'), settings.ETCD.get('port'))
            if action == "add":
                depNum = TenantServiceRelation.objects.filter(tenant_id=tenant_id, service_id=service_id, dep_service_type=tenantS.service_type).count()
                attr = tenantS.service_type.upper()
                if depNum > 0 :
                    attr = attr + "_" + tenantS.service_alias.upper()
                data = {}
                data[attr + "_HOST"] = "127.0.0.1"
                data[attr + "_PORT"] = tenantS.service_port
                data[attr + "_USER"] = "admin"
                data[attr + "_PASSWORD"] = "admin"
                etcdClient.write(etcdPath, json.dumps(data))
                res = etcdClient.get(etcdPath)
                tsr = TenantServiceRelation()
                tsr.tenant_id = tenant_id
                tsr.service_id = service_id
                tsr.dep_service_id = tenantS.service_id
                tsr.dep_service_type = tenantS.service_type
                tsr.dep_order = depNum + 1
                tsr.save()
            elif action == "cancel":
                etcdClient.delete(etcdPath)
                TenantServiceRelation.objects.get(tenant_id=tenant_id, service_id=service_id, dep_service_id=tenantS.service_id).delete()
            result["status"] = "success"    
        except Exception, e:
            logger.exception(e)
            result["status"] = "failure"
        return JsonResponse(result)


class ServiceDetail(AuthedView):
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        result = {}
        try:
            if self.service.deploy_version is None or self.service.deploy_version == "":
                result["totalMemory"] = 0
                result["status"] = "Undeployed"
            else:                
                client = RegionServiceApi()
                body = client.check_service_status(self.service.service_id)
                nodeNum = 0
                runningNum = 0
                isDeploy = 0
                for item in body:
                    nodeNum += 1
                    status = body[item]['status']
                    if status == "Undeployed":
                        isDeploy = -1
                        break
                    elif status == "Running":
                        runningNum += 1
                        isDeploy += 1
                    else:
                        isDeploy += 1                    
                if isDeploy > 0:                
                    if nodeNum == runningNum :
                        if runningNum > 0:
                            result["totalMemory"] = runningNum * self.service.min_memory
                            result["status"] = "Running"
                        else:
                            result["totalMemory"] = 0
                            result["status"] = "Waiting"
                    else:
                        result["totalMemory"] = 0
                        result["status"] = "Waiting"
                elif isDeploy == -1 :
                    result["totalMemory"] = 0
                    result["status"] = "Undeployed"
                else:
                    result["totalMemory"] = 0
                    result["status"] = "Closing"
        except Exception, e:
            logger.info("%s" % e)
            result["totalMemory"] = 0
            result['status'] = "failure"
        return JsonResponse(result)

class ServiceNetAndDisk(AuthedView):
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        result = {}
        try:
            # client = RegionServiceApi()
            # result = client.netAndDiskStatics(self.service.service_id)
            # if len(result)>0 and result["disk"] is not None:
            #    result["disk"] = round(result["disk"] / 1048576, 1)
            #   result["bytesin"] = round(result["bytesin"] / 1024, 1)
            #   result["bytesout"] = round(result["bytesout"] / 1024, 1)
            tenant_id = self.tenant.tenant_id
            service_id = self.service.service_id
            
            tenantServiceStatics = TenantServiceStatics.objects.filter(tenant_id=tenant_id, service_id=service_id).order_by('ID').latest()
            if tenantServiceStatics is not None:
                result["disk"] = tenantServiceStatics.container_disk + tenantServiceStatics.storage_disk
                result["bytesin"] = tenantServiceStatics.net_in
                result["bytesout"] = tenantServiceStatics.net_out
            else:
                result["disk"] = 0
                result["bytesin"] = 0
                result["bytesout"] = 0
        except Exception, e:
            logger.exception(e)
        return JsonResponse(result)

class ServiceLog(AuthedView):
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        try:
            if self.service.deploy_version is None or self.service.deploy_version == "":
                return JsonResponse({})
            else:
                client = RegionServiceApi()
                action = request.GET.get("action", "")
                service_id = self.service.service_id
                tenant_id = self.service.tenant_id
                body = {}
                body["tenant_id"] = tenant_id                    
                if action == "operate":                   
                    body = client.get_userlog(service_id, json.dumps(body))
                    return JsonResponse(body)
                elif action == "service":                    
                    body = client.get_log(service_id, json.dumps(body))
                    return JsonResponse(body)
                return JsonResponse({})
        except Exception as e:
            logger.info("%s" % e)
        return JsonResponse({})
