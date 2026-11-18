export type EntityType='agent'|'task'|'run'|'project'|'workflow'|'knowledge_document'|'conversation'|'file'|'approval'
export type RouteDefinition={path:string;pageId:string;title:string;section:string;parent?:string;description:string;entityType?:EntityType}
export const routes:RouteDefinition[]=[
{path:'/app',pageId:'dashboard',title:'工作台',section:'总览',description:'平台运行概况与快捷入口'},
{path:'/403',pageId:'forbidden',title:'无权访问',section:'错误',description:'当前会话没有访问此页面的权限'},
{path:'/agents',pageId:'agents',title:'智能体',section:'构建',description:'智能体列表与配置',entityType:'agent'},
{path:'/agents/:id',pageId:'agent-detail',title:'智能体详情',section:'构建',parent:'/agents',description:'智能体配置',entityType:'agent'},
{path:'/workflows',pageId:'workflows',title:'工作流',section:'构建',description:'工作流定义与运行',entityType:'workflow'},
{path:'/tasks',pageId:'tasks',title:'任务中心',section:'运行',description:'任务队列与执行状态',entityType:'task'},
{path:'/runs',pageId:'runs',title:'运行记录',section:'运行',description:'Agent 运行记录',entityType:'run'},
{path:'/projects',pageId:'projects',title:'项目空间',section:'资源',description:'项目与工作区',entityType:'project'},
{path:'/knowledge',pageId:'knowledge',title:'知识库',section:'资源',description:'知识文档与索引',entityType:'knowledge_document'},
{path:'/conversations',pageId:'conversations',title:'会话',section:'资源',description:'对话与消息记录',entityType:'conversation'},
{path:'/files',pageId:'files',title:'文件资产',section:'资源',description:'上传文件与解析结果',entityType:'file'},
{path:'/approvals',pageId:'approvals',title:'审批中心',section:'运行',description:'待处理审批',entityType:'approval'},
{path:'/settings/models',pageId:'model-settings',title:'模型配置',section:'管理',description:'Provider、模型与路由策略'},
{path:'/settings/system',pageId:'system-settings',title:'系统设置',section:'管理',description:'实例配置与系统参数'},
{path:'/admin/organization',pageId:'ADM-ORG',title:'组织与成员',section:'平台管理',description:'组织、部门、项目与成员生命周期'},
{path:'/admin/access-control',pageId:'ADM-RBAC',title:'角色与权限',section:'平台管理',description:'角色权限矩阵与数据范围策略'}]
export function resolveRoute(pathname:string):RouteDefinition|undefined{
 const path=pathname.replace(/\/+$/,'')||'/'
 if(path==='/')return routes[0]
 return routes.find(route=>{
  const pattern=route.path.split('/').map(part=>part.charAt(0)===':'?'[^/]+':part).join('/')
  return new RegExp('^'+pattern+'/?$').test(path)
 })
}
export function breadcrumbs(route:RouteDefinition):RouteDefinition[]{
 const parent=route.parent?routes.find(item=>item.path===route.parent):undefined
 return parent?[routes[0],parent,route]:[routes[0],route]
}
