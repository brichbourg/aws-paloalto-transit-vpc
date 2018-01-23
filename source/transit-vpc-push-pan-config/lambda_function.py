######################################################################################################################
#  Copyright 2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.                                           #
#                                                                                                                    #
#  Licensed under the Amazon Software License (the "License"). You may not use this file except in compliance        #
#  with the License. A copy of the License is located at                                                             #
#                                                                                                                    #
#      http://aws.amazon.com/asl/                                                                                    #
#                                                                                                                    #   
#  or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES #
#  OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions    #
#  and limitations under the License.                                                                                #
######################################################################################################################

import boto3
from botocore.client import Config
import paramiko
from xml.dom import minidom
import ast
import time
import os
import logging
log = logging.getLogger()
log.setLevel(logging.INFO)

config_file = 'transit_vpc_config.txt'
#These S3 endpoint URLs are provided to support VPC endpoints for S3 in regions such as Frankfort that require explicit-
#region endpoint definition
endpoint_url = {
    "us-east-1"         : "https://s3.amazonaws.com",
    "us-east-2"         : "https://s3-us-east-2.amazonaws.com",
    "us-west-1"         : "https://s3-us-west-1.amazonaws.com",
    "us-west-2"         : "https://s3-us-west-2.amazonaws.com",
    "eu-west-1"         : "https://s3-eu-west-1.amazonaws.com",
    "eu-central-1"      : "https://s3-eu-central-1.amazonaws.com",
    "ap-northeast-1"    : "https://s3-ap-northeast-1.amazonaws.com",
    "ap-northeast-2"    : "https://s3-ap-northeast-2.amazonaws.com",
    "ap-south-1"        : "https://s3-ap-south-1.amazonaws.com",
    "ap-southeast-1"    : "https://s3-ap-southeast-1.amazonaws.com",
    "ap-southeast-2"    : "https://s3-ap-southeast-2.amazonaws.com",
    "sa-east-1"         : "https://s3-sa-east-1.amazonaws.com"
}

#Logic to determine when the prompt has been discovered
def prompt(chan):
    buff = ''
    while not (buff.endswith('% ') or buff.endswith('> ') or buff.endswith('# ')):
        resp = chan.recv(9999)
        buff += resp
        #log.info("response: %s",resp)
    return buff

# Logic to figure out the next available tunnel
def getNextTunnelId(ssh):
    log.info('Start getNextTunnelId')
    output = ''
    prompt(ssh)
    ssh.send('show interface logical \n')
    output = prompt(ssh)

    lastTunnelNum = ''
    for line in output.split('\n'):
        log.info('line: %s',line)
        if line.strip()[:7] == 'tunnel.':
            log.info("%s", line)
            lastTunnelNum = line.strip().partition(' ')[0].replace('tunnel.','')

    ssh.send('exit\n')

    if lastTunnelNum == '':
        return 1
    else:
        return 1 + int(lastTunnelNum)

# Logic to figure out existing tunnel IDs
def getExistingTunnelId(ssh, vpn_connection_id, tvar):
    log.info('Start getExistingTunnelId')
    output = ''
    prompt(ssh)
    ssh.send('show interface logical \n')
    output = prompt(ssh)

    lastTunnelNum = ''
    for line in output.split('\n'):
        log.info('line: %s',line)
        if line.strip()[:7] == 'tunnel.':
            log.info("%s", line)
            lastTunnelNum = line.strip().partition(' ')[0].replace('tunnel.','')

    ssh.send('exit\n')

    if lastTunnelNum == '':
        return 0
    else:
        return int(lastTunnelNum)

#Generic logic to push pre-generated config to the router
def pushConfig(ssh, config):
    ssh.send('configure\n')
    log.debug("%s", prompt(ssh))
    stime = time.time()
    for line in config:
        if line == "WAIT":
            log.debug("Waiting 30 seconds...")
            time.sleep(30)
        else:
            ssh.send(line+'\n')
            log.info("%s", prompt(ssh))
    
    log.info("Saving backup config...")
    ssh.send('save config to AWS_config.txt\n\n\n\n\n')
    log.info("Backup configuration saved")
    time.sleep(15)

    log.info("Committing Configuration...")
    ssh.send('commit\n')
    time.sleep(30)
    ssh.send('exit\n')

    log.debug("   ... %s seconds ...", (time.time() - stime))

    ssh.send('exit\n')

    log.info("Config Update complete!")

#Logic to determine the bucket prefix from the S3 key name that was provided
def getBucketPrefix(bucket_name, bucket_key):
    #Figure out prefix from known bucket_name and bucket_key
    bucket_prefix = '/'.join(bucket_key.split('/')[:-2])
    if len(bucket_prefix) > 0:
        bucket_prefix += '/'
    return bucket_prefix

#Logic to download the transit VPC configuration file from S3
def getTransitConfig(bucket_name, bucket_prefix, s3_url, config_file):
    s3 = boto3.client('s3', endpoint_url=s3_url,
                      config=Config(s3={'addressing_style': 'virtual'}, signature_version='s3v4'))
    log.info("Downloading config file: %s/%s/%s%s", s3_url, bucket_name, bucket_prefix, config_file)
    return ast.literal_eval(s3.get_object(Bucket=bucket_name,Key=bucket_prefix+config_file)['Body'].read())

#Logic to upload a new/updated transit VPC configuration file to S3 (not currently used)
def putTransitConfig(bucket_name, bucket_prefix, s3_url, config_file, config):
    s3=boto3.client('s3', endpoint_url=s3_url,
                    config=Config(s3={'addressing_style': 'virtual'}, signature_version='s3v4'))
    log.info("Uploading new config file: %s/%s/%s%s", s3_url,bucket_name, bucket_prefix,config_file)
    s3.put_object(Bucket=bucket_name,Key=bucket_prefix+config_file,Body=str(config))

#Logic to download the SSH private key from S3 to be used for SSH public key authentication
def downloadPrivateKey(bucket_name, bucket_prefix, s3_url, prikey):
    if os.path.exists('/tmp/'+prikey):
        os.remove('/tmp/'+prikey)
    s3=boto3.client('s3', endpoint_url=s3_url,
                    config=Config(s3={'addressing_style': 'virtual'}, signature_version='s3v4'))
    log.info("Downloading private key: %s/%s/%s%s",s3_url, bucket_name, bucket_prefix, prikey)
    s3.download_file(bucket_name,bucket_prefix+prikey, '/tmp/'+prikey)

#Logic to create the appropriate PaloAlto configuration
def create_paloalto_config(bucket_name, bucket_key, s3_url, bgp_asn, ssh):
    log.info("Processing %s/%s", bucket_name, bucket_key)

    #Download the VPN configuration XML document
    s3=boto3.client('s3',endpoint_url=s3_url,
                    config=Config(s3={'addressing_style': 'virtual'}, signature_version='s3v4'))
    log.info("s3 %s", s3)
    config=s3.get_object(Bucket=bucket_name,Key=bucket_key)
    log.info("Config %s", config)
    xmldoc=minidom.parseString(config['Body'].read())
    log.info("xmldoc %s", xmldoc)
    #Extract transit_vpc_configuration values
    vpn_config = xmldoc.getElementsByTagName("transit_vpc_config")[0]
    log.info("vpn_config %s", vpn_config)
    account_id = vpn_config.getElementsByTagName("account_id")[0].firstChild.data
    log.info("account_id %s", account_id)
    vpn_endpoint = vpn_config.getElementsByTagName("vpn_endpoint")[0].firstChild.data
    log.info("vpn_endpoint %s", vpn_endpoint)
    vpn_status = vpn_config.getElementsByTagName("status")[0].firstChild.data
    log.info("vpn_status %s", vpn_status)
    preferred_path = vpn_config.getElementsByTagName("preferred_path")[0].firstChild.data
    log.info("preferred_path %s", preferred_path)

    #Extract VPN connection information
    vpn_connection=xmldoc.getElementsByTagName('vpn_connection')[0]
    log.info("vpn_connection %s", vpn_connection)
    vpn_connection_id=vpn_connection.attributes['id'].value
    log.info("vpn_connection_id %s", vpn_connection_id)
    customer_gateway_id=vpn_connection.getElementsByTagName("customer_gateway_id")[0].firstChild.data
    log.info("customer_gateway_id %s", customer_gateway_id)
    vpn_gateway_id=vpn_connection.getElementsByTagName("vpn_gateway_id")[0].firstChild.data
    log.info("vpn_gateway_id %s", vpn_gateway_id)
    vpn_connection_type=vpn_connection.getElementsByTagName("vpn_connection_type")[0].firstChild.data
    log.info("vpn_connection_type %s", vpn_connection_type)

    tunnelId=0
    #Determine the VPN tunnels to work with
    if vpn_status == 'create':    
        tunnelId=getNextTunnelId(ssh)
    '''
    else:
        tunnelId=getExistingTunnelId(ssh,vpn_connection_id)
        if tunnelId == 0:
            return
    '''

    log.info("%s %s with tunnel #%s and #%s.",vpn_status, vpn_connection_id, tunnelId, tunnelId+1)
    if vpn_status == 'delete':
        config_text = []
        config_text.append('configure \n')

        ipsec_tunnel_var = 0
        for ipsec_tunnel in vpn_connection.getElementsByTagName("ipsec_tunnel"):
            ipsec_tunnel_var += 1
            tunnelId=getExistingTunnelId(ssh,vpn_connection_id,ipsec_tunnel_var)
            if tunnelId == 0:
                return

            config_text.append('delete network tunnel ipsec ipsec-tunnel-{} auto-key ike-gateway ike-vpn-{}-{}'.format(tunnelId,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('delete network ike gateway ike-vpn-{}-{}'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('delete network ike crypto-profiles ipsec-crypto-profiles ipsec-vpn-{}-{}'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('delete network ike crypto-profiles ike-crypto-profiles ike-crypto-vpn-{}'.format(vpn_connection_id,tunnelId))
            config_text.append('delete network virtual-router default protocol bgp peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{}'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('delete network virtual-router default interface tunnel.{}'.format(tunnelId))
            config_text.append('delete zone untrust network layer3 tunnel.{}'.format(tunnelId))
            config_text.append('delete network interface tunnel units tunnel.{}'.format(tunnelId))
    else:
        config_text = []
        config_text.append('configure \n')

        ipsec_tunnel_var = 0
          # Create tunnel specific configuration
        for ipsec_tunnel in vpn_connection.getElementsByTagName("ipsec_tunnel"):
            ipsec_tunnel_var += 1
            customer_gateway=ipsec_tunnel.getElementsByTagName("customer_gateway")[0]
            log.info("customer_gateway %s", customer_gateway)
            customer_gateway_tunnel_outside_address=customer_gateway.getElementsByTagName("tunnel_outside_address")[0].getElementsByTagName("ip_address")[0].firstChild.data
            log.info("customer_gateway_tunnel_outside_address %s", customer_gateway_tunnel_outside_address)
            customer_gateway_tunnel_inside_address_ip_address=customer_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("ip_address")[0].firstChild.data
            log.info("customer_gateway_tunnel_inside_address_ip_address %s", customer_gateway_tunnel_inside_address_ip_address)
            customer_gateway_tunnel_inside_address_network_mask=customer_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("network_mask")[0].firstChild.data
            log.info("customer_gateway_tunnel_inside_address_network_mask %s", customer_gateway_tunnel_inside_address_network_mask)
            customer_gateway_tunnel_inside_address_network_cidr=customer_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("network_cidr")[0].firstChild.data
            log.info("customer_gateway_tunnel_inside_address_network_cidr %s", customer_gateway_tunnel_inside_address_network_cidr)
            customer_gateway_bgp_asn=customer_gateway.getElementsByTagName("bgp")[0].getElementsByTagName("asn")[0].firstChild.data
            log.info("customer_gateway_bgp_asn %s", customer_gateway_bgp_asn)
            customer_gateway_bgp_hold_time=customer_gateway.getElementsByTagName("bgp")[0].getElementsByTagName("hold_time")[0].firstChild.data
            log.info("customer_gateway_bgp_hold_time %s", customer_gateway_bgp_hold_time)

            vpn_gateway=ipsec_tunnel.getElementsByTagName("vpn_gateway")[0]
            log.info("vpn_gateway %s", vpn_gateway)
            vpn_gateway_tunnel_outside_address=vpn_gateway.getElementsByTagName("tunnel_outside_address")[0].getElementsByTagName("ip_address")[0].firstChild.data
            log.info("vpn_gateway_tunnel_outside_address %s", vpn_gateway_tunnel_outside_address)
            vpn_gateway_tunnel_inside_address_ip_address=vpn_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("ip_address")[0].firstChild.data
            log.info("vpn_gateway_tunnel_inside_address_ip_address %s", vpn_gateway_tunnel_inside_address_ip_address)
            vpn_gateway_tunnel_inside_address_network_mask=vpn_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("network_mask")[0].firstChild.data
            log.info("vpn_gateway_tunnel_inside_address_network_mask %s", vpn_gateway_tunnel_inside_address_network_mask)
            vpn_gateway_tunnel_inside_address_network_cidr=vpn_gateway.getElementsByTagName("tunnel_inside_address")[0].getElementsByTagName("network_cidr")[0].firstChild.data
            log.info("vpn_gateway_tunnel_inside_address_network_cidr %s", vpn_gateway_tunnel_inside_address_network_cidr)
            vpn_gateway_bgp_asn=vpn_gateway.getElementsByTagName("bgp")[0].getElementsByTagName("asn")[0].firstChild.data
            log.info("vpn_gateway_bgp_asn %s", vpn_gateway_bgp_asn)
            vpn_gateway_bgp_hold_time=vpn_gateway.getElementsByTagName("bgp")[0].getElementsByTagName("hold_time")[0].firstChild.data
            log.info("vpn_gateway_bgp_hold_time %s", vpn_gateway_bgp_hold_time)

            ike=ipsec_tunnel.getElementsByTagName("ike")[0]
            log.info("ike %s", ike)
            ike_authentication_protocol=ike.getElementsByTagName("authentication_protocol")[0].firstChild.data
            log.info("ike_authentication_protocol %s", ike_authentication_protocol)
            ike_encryption_protocol=ike.getElementsByTagName("encryption_protocol")[0].firstChild.data
            log.info("ike_encryption_protocol %s", ike_encryption_protocol)
            ike_lifetime=ike.getElementsByTagName("lifetime")[0].firstChild.data
            log.info("ike_lifetime %s", ike_lifetime)
            ike_perfect_forward_secrecy=ike.getElementsByTagName("perfect_forward_secrecy")[0].firstChild.data
            log.info("ike_perfect_forward_secrecy %s", ike_perfect_forward_secrecy)
            ike_mode=ike.getElementsByTagName("mode")[0].firstChild.data
            log.info("ike_mode %s", ike_mode)
            ike_pre_shared_key=ike.getElementsByTagName("pre_shared_key")[0].firstChild.data
            log.info("ike_pre_shared_key %s", ike_pre_shared_key)
            
            ipsec=ipsec_tunnel.getElementsByTagName("ipsec")[0]
            log.info("ipsec %s", ipsec)
            ipsec_protocol=ipsec.getElementsByTagName("protocol")[0].firstChild.data
            log.info("ipsec_protocol %s", ipsec_protocol)
            ipsec_authentication_protocol=ipsec.getElementsByTagName("authentication_protocol")[0].firstChild.data
            log.info("ipsec_authentication_protocol %s", ipsec_authentication_protocol)
            ipsec_encryption_protocol=ipsec.getElementsByTagName("encryption_protocol")[0].firstChild.data
            log.info("ipsec_encryption_protocol %s", ipsec_encryption_protocol)
            ipsec_lifetime=ipsec.getElementsByTagName("lifetime")[0].firstChild.data
            log.info("ipsec_lifetime %s", ipsec_lifetime)
            ipsec_perfect_forward_secrecy=ipsec.getElementsByTagName("perfect_forward_secrecy")[0].firstChild.data
            log.info("ipsec_perfect_forward_secrecy %s", ipsec_perfect_forward_secrecy)
            ipsec_mode=ipsec.getElementsByTagName("mode")[0].firstChild.data
            log.info("ipsec_mode %s", ipsec_mode)
            ipsec_clear_df_bit=ipsec.getElementsByTagName("clear_df_bit")[0].firstChild.data
            log.info("ipsec_clear_df_bit %s", ipsec_clear_df_bit)
            ipsec_fragmentation_before_encryption=ipsec.getElementsByTagName("fragmentation_before_encryption")[0].firstChild.data
            log.info("ipsec_fragmentation_before_encryption %s", ipsec_fragmentation_before_encryption)
            ipsec_tcp_mss_adjustment=ipsec.getElementsByTagName("tcp_mss_adjustment")[0].firstChild.data
            log.info("ipsec_tcp_mss_adjustment %s", ipsec_tcp_mss_adjustment)
            ipsec_dead_peer_detection_interval=ipsec.getElementsByTagName("dead_peer_detection")[0].getElementsByTagName("interval")[0].firstChild.data
            log.info("ipsec_dead_peer_detection_interval %s", ipsec_dead_peer_detection_interval)
            ipsec_dead_peer_detection_retries=ipsec.getElementsByTagName("dead_peer_detection")[0].getElementsByTagName("retries")[0].firstChild.data
            log.info("ipsec_dead_peer_detection_retries %s", ipsec_dead_peer_detection_retries)

            config_text.append('set network ike crypto-profiles ike-crypto-profiles ike-crypto-vpn-{}-{} dh-group group2'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike crypto-profiles ike-crypto-profiles ike-crypto-vpn-{}-{} hash sha1'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike crypto-profiles ike-crypto-profiles ike-crypto-vpn-{}-{} lifetime seconds 28800'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike crypto-profiles ike-crypto-profiles ike-crypto-vpn-{}-{} encryption aes-128-cbc'.format(vpn_connection_id,ipsec_tunnel_var))

            config_text.append('set network ike gateway ike-vpn-{}-{} protocol ikev1 ike-crypto-profile ike-crypto-vpn-{}-{} exchange-mode main'.format(vpn_connection_id,ipsec_tunnel_var,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike gateway ike-vpn-{}-{} protocol ikev1 dpd interval 10 retry 3 enable yes'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike gateway ike-vpn-{}-{} authentication pre-shared-key key {}'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike gateway ike-vpn-{}-{} local-address ip {}'.format(vpn_connection_id,ipsec_tunnel_var,vpn_gateway_tunnel_outside_address))
            config_text.append('set network ike gateway ike-vpn-{}-{} local-address interface ethernet1/1'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike gateway ike-vpn-{}-{} peer-address ip {}'.format(vpn_connection_id,ipsec_tunnel_var,vpn_gateway_tunnel_inside_address))

            config_text.append('set network ike crypto-profiles ipsec-crypto-profiles ipsec-vpn-{}-{} esp authentication sha1'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike crypto-profiles ipsec-crypto-profiles ipsec-vpn-{}-{} esp encryption aes-128-cbc'.format(vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network ike crypto-profiles ipsec-crypto-profiles ipsec-vpn-{}-{} dh-group group2 lifetime seconds 3600'.format(vpn_connection_id,ipsec_tunnel_var))

            config_text.append('set network interface tunnel units tunnel.{} ip {}/{}'.format(tunnelId,customer_gateway_tunnel_inside_address_ip_address,vpn_gateway_tunnel_inside_address_network_cidr))
            config_text.append('set network interface tunnel units tunnel.{} mtu 1427'.format(tunnelId))

            config_text.append('set zone untrust network layer3 tunnel.{}'.format(tunnelId))

            config_text.append('set network virtual-router default interface tunnel.{}'.format(tunnelId))

            config_text.append('set network tunnel ipsec ipsec-tunnel-{} auto-key ipsec-crypto-profile ipsec-vpn-{}-{}'.format(ipsec_tunnel_var,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network tunnel ipsec ipsec-tunnel-{} auto-key ike-gateway ike-vpn-{}-{}'.format(ipsec_tunnel_var,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network tunnel ipsec ipsec-tunnel-{} tunnel-interface tunnel.{}'.format(ipsec_tunnel_var,tunnelId))
            config_text.append('set network tunnel ipsec ipsec-tunnel-{} anti-replay yes'.format(ipsec_tunnel_var,tunnelId))

            config_text.append('set network virtual-router default protocol bgp router-id {}'.format(vpn_gateway_tunnel_outside_address))
            config_text.append('set network virtual-router default protocol bgp install-route yes')
            config_text.append('set network virtual-router default protocol bgp enable yes')
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} peer-as {}'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var,customer_gateway_bgp_asn))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} connection-options keep-alive-interval 10'.format(vpn_connection_id,ipsec_tunnel_var,vpn_gateway_bgp_asn))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} connection-options hold-time 30'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} enable yes'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} local-address ip {}/{}'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var,customer_gateway_tunnel_inside_address_ip_address,vpn_gateway_tunnel_inside_address_network_cidr))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} local-address interface tunnel.{}'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var,tunnelId))
            config_text.append('set network virtual-router default protocol bgp local-as {} peer-group AmazonBGP peer amazon-tunnel-vpn-{}-{} peer-address ip {}'.format(vpn_gateway_bgp_asn,vpn_connection_id,ipsec_tunnel_var,vpn_gateway_tunnel_inside_address_ip_address))

            config_text.append('set network virtual-router default protocol redist-profile Default_to_VPC filter type static')
            config_text.append('set network virtual-router default protocol redist-profile Default_to_VPC filter destination 0.0.0.0/0')
            config_text.append('set network virtual-router default protocol redist-profile Default_to_VPC priority 10')
            config_text.append('set network virtual-router default protocol redist-profile Default_to_VPC action redist')
            config_text.append('set network virtual-router default protocol bgp allow-redist-default-route yes')
            config_text.append('set network virtual-router default protocol bgp redist-rules Default_to_VPC enable yes')
            config_text.append('set network virtual-router default protocol bgp redist-rules Default_to_VPC set-origin incomplete')

            tunnelId+=1
    log.debug("Conversion complete")
    return config_text

def lambda_handler(event, context):
    record=event['Records'][0]
    bucket_name=record['s3']['bucket']['name']
    bucket_key=record['s3']['object']['key']
    bucket_region=record['awsRegion']
    bucket_prefix=getBucketPrefix(bucket_name, bucket_key)
    log.debug("Getting config")
    stime = time.time()
    config = getTransitConfig(bucket_name, bucket_prefix, endpoint_url[bucket_region], config_file)
    if 'PAVM1' in bucket_key:
        pavm_ip=config['PIP1']
        pavm_name='PAVM1'
    else:
        pavm_ip=config['PIP2']
        pavm_name='PAVM2'
    log.info("--- %s seconds ---", (time.time() - stime))
    #Download private key file from secure S3 bucket
    downloadPrivateKey(bucket_name, bucket_prefix, endpoint_url[bucket_region], config['PRIVATE_KEY'])
    log.debug("Reading downloaded private key into memory.")
    k = paramiko.RSAKey.from_private_key_file("/tmp/"+config['PRIVATE_KEY'])
    #Delete the temp copy of the private key
    os.remove("/tmp/"+config['PRIVATE_KEY'])
    log.debug("Deleted downloaded private key.")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    log.info("Connecting to %s (%s)", pavm_name, pavm_ip)
    stime = time.time()
    try:
        c.connect( hostname = pavm_ip, username = config['USER_NAME'], pkey = k )
        PubKeyAuth=True
    except paramiko.ssh_exception.AuthenticationException:
        log.error("PubKey Authentication Failed! Connecting with password")
        c.connect( hostname = pavm_ip, username = config['USER_NAME'], password = config['PASSWORD'] )
        PubKeyAuth=False
    #Need to handle the generic exception case which most likely happens due to single ssh connection restriction.
    #If still no luck after 15 minutes, let it time out. 
    except :
        i = 0
        while(time.time() - stime < 900):
            i = i + 1
            time.sleep(5)
            try:
                c.connect( hostname = pavm_ip, username = config['USER_NAME'], pkey = k )
                break
            except:
                pass
        if(time.time() - stime < 900):
            log.info("Connected to %s after %s retry(retries)", pavm_name, i)
        else:
            log.info("Connection to %s timedout. Stopped retrying after 15 minutes", pavm_name)
            c.close()
            raise ValueError( "Operation timed out!!" )

    log.info("--- %s seconds ---", (time.time() - stime))
    log.info("Connected to %s",pavm_ip)
    ssh = c.invoke_shell()
    log.info("%s",prompt(ssh))
    log.info("Creating config.")
    log.info("bucket_name: %s", bucket_name)
    log.info("bucket_key: %s", bucket_key)
    log.info("endpoint_url[bucket_region]: %s", endpoint_url[bucket_region])
    log.info("config['BGP_ASN']: %s", config['BGP_ASN'])
    stime = time.time()
    pavm_config = create_paloalto_config(bucket_name, bucket_key, endpoint_url[bucket_region], config['BGP_ASN'], ssh)
    log.info("--- %s seconds ---", (time.time() - stime))
    log.info("Pushing config to router.")
    stime = time.time()
    pushConfig(ssh,pavm_config)
    log.info("--- %s seconds ---", (time.time() - stime))
    ssh.close()
    #Close the ssh client as well
    c.close()

    return
    {
        'message' : "Script execution completed. See Cloudwatch logs for complete output"
    }
