# Transit VPC w/ Palo Alto Firewalls

I forked this from https://github.com/bucklander/aws-paloalto-transit-vpc and created a separate CFn to get the Poller and Configurator Lambda functions working.  **This Repo does assume the user is familar with the AWS Transit VPC reference architecture based on Cisco CSRs.**

Source code for the AWS Transit VPC customized to use Palo Alto VM appliances versus Cisco's CSR. The goal with this project is to insert NGFW inspection and ultimately better network visibility into all inter-VPC communication, something lacking in the existing Cisco design.  
Forked from https://github.com/awslabs/aws-transit-vpc  

*Disclaimer: This personal project is in no way associated, officially or unofficially, with Palo Alto Networks. Use at your own risk.*

## Cloudformation templates

- transit-vpc-primary-account.template
- transit-vpc-second-account.template
- transit-Palo-Poller-Configurator.yaml -- This is the Cloudformation Template I created for deploying the Poller and Configurator Lambda functions

## Lambda source code

- transit-vpc-poller.py
- transit-vpc-push-paloalto-config.py

## Install

- Build the Lambda packages.  The source bucket name will be used later where you will have to reference it in the .yaml file.

		cd deployment
		./build-s3-dist.sh source-bucket-base-name 


- Create an S3 bucket with the name you used in the build script.  The script will append the region name on it.  For example, source-bucket-base-name-us-east-1

- Upload the two ZIP files in the /dist/ directory that was created by the script to the s3 bucket under the location or key name `/transit-vpc/latest/`

- Deploy the CFn template you want to use.  I.E. transit-vpc-primary-account.template.  This will deploy the base stack with most of the Transit VPC components for the Palo Alto firewalls to function.

- Once the stack is deployed, open the `transit-Palo-Poller-Configurator.yaml` in a text editor

- Edit the following mapping (you will have to get these manually via the AWS Console):
	- Function --> General --> S3Bucket
	- Function --> General --> VPNConfigBucketName
	- Function --> General --> FunctionSecurityGroupName
	- Function --> Configurator --> ConfiguratorIAMRoleName
	- Function --> Configurator --> ConfiguratorSubnet1
	- Function --> Configurator --> ConfiguratorSubnet2
	- Function --> Configurator --> VPNConfigBucketName
	- Function --> Poller --> PollerIAMRoleName

- Deploy this YAML file as another CFn stack.

- Once the stack deploys, open the Poller Lambda function that was created.

- Add a CloudWatch Events trigger and use the rule that is named based on the stack you just created.  I.E. `stackname-PollerEvent-ASASDSDSAD`

- Hopefully this works!








