"""Publish an existing JX3 image only after verifying a private Docker Hub repo.

Run on the NAS with Docker access. PAT is read interactively, never from arguments.
"""
import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


class PublishError(RuntimeError):
    pass


def json_request(path, payload=None, bearer=None, missing_ok=False):
    headers={'Accept':'application/json'}
    if payload is not None:headers['Content-Type']='application/json'
    if bearer:headers['Authorization']='Bearer '+bearer
    request=urllib.request.Request('https://hub.docker.com'+path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code==404:return None
        raise PublishError('Docker Hub API '+path+' HTTP '+str(error.code)+'；检查仓库是否存在及 Token 权限') from None
    except (urllib.error.URLError,ValueError):
        raise PublishError('Docker Hub 请求失败，请检查网络或稍后重试') from None


def require_visibility(info, namespace, repository, visibility):
    expected_private = visibility == 'private'
    if not isinstance(info,dict) or info.get('namespace')!=namespace or info.get('name')!=repository or info.get('is_private') is not expected_private:
        raise PublishError('仓库身份或可见性与预期不符，停止发布')
    if info.get('permissions',{}).get('write') is False:
        raise PublishError('当前 Token 没有仓库写入权限')


def require_private(info, namespace, repository):
    require_visibility(info, namespace, repository, 'private')


def command(args, capture=True, secret=None):
    result=subprocess.run(args,input=secret,text=True,capture_output=capture)
    if result.returncode:
        raise PublishError('Docker 操作失败（退出码 '+str(result.returncode)+'），未继续后续发布步骤')
    return result.stdout.strip() if capture else ''


def main():
    parser=argparse.ArgumentParser(description='复用现有镜像，验证私有仓库后发布固定版本和 stable；不更新运行中的应用')
    parser.add_argument('--repository',default='jimmeyoumu/jx3-equipment-manager')
    parser.add_argument('--version',required=True)
    parser.add_argument('--container',default='jx3-equipment-app-1')
    parser.add_argument('--source-image')
    parser.add_argument('--create-private',action='store_true',help='仓库不存在时明确创建私有仓库，随后重新核对私有状态')
    parser.add_argument('--visibility', choices=('private','public'), default='private', help='明确要求的仓库可见性，默认仍为 private')
    parser.add_argument('--check-only',action='store_true',help='仅检查本地镜像版本与大小，不登录或上传')
    args=parser.parse_args()
    if args.create_private and args.visibility != 'private':
        parser.error('--create-private 仅用于私有仓库')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*',args.repository):
        parser.error('仓库应为 Docker Hub 用户名/仓库名')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',args.version) or args.version in ('stable','latest'):
        parser.error('请填写固定发布版本，例如 0.1.1')
    docker=shutil.which('docker') or '/usr/local/bin/docker'
    source=args.source_image or command([docker,'inspect','--format','{{.Image}}',args.container])
    info=json.loads(command([docker,'image','inspect',source]))[0]
    version=command([docker,'run','--rm','--network','none','--read-only','--tmpfs','/app/data:rw,mode=755','--entrypoint','python',source,'-c','from backend.main import app; print(app.version)'])
    if version!=args.version:
        raise PublishError('镜像内应用版本 '+version+' 与发布版本 '+args.version+' 不一致，停止发布')
    print('源镜像：'+info['Id'])
    print('运行平台：'+info['Os']+'/'+info['Architecture']+'；未压缩大小：'+format(info['Size']/1024/1024,'.1f')+' MiB')
    print('目标：'+args.repository+':'+args.version+' 和 :stable（要求 '+args.visibility+' 仓库）')
    if args.check_only:
        print('本地检查通过，未登录 Docker Hub、未上传、未修改线上容器。')
        return
    if not sys.stdin.isatty():
        raise PublishError('请从交互终端运行，脚本不接受明文 Token 参数或环境变量')
    namespace,repository=args.repository.split('/')
    secret=getpass.getpass('Docker Hub Access Token（输入隐藏）：')
    if not secret:raise PublishError('未输入 Token，停止发布')
    auth=json_request('/v2/auth/token',{'identifier':namespace,'secret':secret})
    bearer=auth.get('access_token')
    if not isinstance(bearer,str) or not bearer:raise PublishError('认证响应缺少 access_token')
    repo_path='/v2/namespaces/'+namespace+'/repositories/'+repository
    info=json_request(repo_path,bearer=bearer,missing_ok=True)
    if info is None and args.create_private:
        json_request('/v2/namespaces/'+namespace+'/repositories',{'name':repository,'namespace':namespace,'is_private':True,'registry':'docker.io','description':'Private JX3 equipment manager'},bearer=bearer)
        info=json_request(repo_path,bearer=bearer)
    require_visibility(info,namespace,repository,args.visibility)
    if json_request(repo_path+'/tags/'+args.version,bearer=bearer,missing_ok=True) is not None:
        raise PublishError('此固定版本已发布，不覆盖。若上次仅 stable 推送失败，请先核对远端版本后再单独续传 stable')
    # Docker stores auth only in this temporary 0700 directory; cleanup on exit.
    with tempfile.TemporaryDirectory(prefix='jx3-hub-auth-') as auth_dir:
        command([docker,'--config',auth_dir,'login','--username',namespace,'--password-stdin'],secret=secret+'\n')
        secret=None
        for tag in (args.version,'stable'):
            require_visibility(json_request(repo_path,bearer=bearer),namespace,repository,args.visibility)
            target=args.repository+':'+tag
            command([docker,'--config',auth_dir,'tag',source,target])
            command([docker,'--config',auth_dir,'push',target],capture=False)
        require_visibility(json_request(repo_path,bearer=bearer),namespace,repository,args.visibility)
    print('两个标签已推送到预期仓库。当前运行容器未改变；尚需验证拉取后再切换 Compose。')


if __name__=='__main__':
    try:main()
    except (PublishError,OSError,KeyboardInterrupt) as error:
        print('发布未完成：'+str(error),file=sys.stderr)
        raise SystemExit(1)
