import boto3
from botocore.config import Config
from app.config import settings
from typing import BinaryIO, Union
import uuid
from datetime import datetime
import io

class R2Service:
    def __init__(self):
        # Validate R2 settings
        if not settings.R2_ENDPOINT_URL:
            raise ValueError("R2_ENDPOINT_URL is not set in environment variables")
        if not settings.R2_ACCESS_KEY:
            raise ValueError("R2_ACCESS_KEY is not set in environment variables")
        if not settings.R2_SECRET_KEY:
            raise ValueError("R2_SECRET_KEY is not set in environment variables")
        
        # Use R2_BUCKET_NAME if available, otherwise extract from R2_BUCKET_URL
        if settings.R2_BUCKET_NAME:
            self.bucket_name = settings.R2_BUCKET_NAME
        elif settings.R2_BUCKET_URL:
            # Extract bucket name from URL
            # Format: https://bucket-name.r2.cloudflarestorage.com or https://pub-xxxxx.r2.dev/bucket-name
            if '/' in settings.R2_BUCKET_URL:
                # Remove protocol and domain, get bucket name
                parts = settings.R2_BUCKET_URL.replace('https://', '').replace('http://', '').split('/')
                if len(parts) > 1:
                    self.bucket_name = parts[-1].split('?')[0]
                else:
                    # If it's just domain, try to extract from subdomain
                    domain_parts = parts[0].split('.')
                    if domain_parts[0] and domain_parts[0] != 'r2':
                        self.bucket_name = domain_parts[0]
                    else:
                        raise ValueError(f"Could not extract bucket name from R2_BUCKET_URL: {settings.R2_BUCKET_URL}")
            else:
                self.bucket_name = settings.R2_BUCKET_URL
        else:
            raise ValueError("Either R2_BUCKET_NAME or R2_BUCKET_URL must be set in environment variables")
        
        print(f"R2 Configuration: Endpoint={settings.R2_ENDPOINT_URL}, Bucket={self.bucket_name}")
        
        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            config=Config(signature_version='s3v4')
        )
    
    def upload_file(self, file: Union[BinaryIO, bytes, io.BytesIO], filename: str, folder: str = "documents") -> str:
        """Upload file to R2 and return public URL"""
        try:
            # Generate unique filename
            file_ext = filename.split('.')[-1] if '.' in filename else ''
            unique_filename = f"{folder}/{uuid.uuid4()}.{file_ext}" if file_ext else f"{folder}/{uuid.uuid4()}"
            
            # Convert bytes to file-like object if needed
            if isinstance(file, bytes):
                file = io.BytesIO(file)
            
            # Reset file pointer to beginning
            if hasattr(file, 'seek'):
                file.seek(0)
            
            print(f"Uploading to R2: bucket={self.bucket_name}, key={unique_filename}")
            
            # Upload to R2
            self.s3_client.upload_fileobj(
                file,
                self.bucket_name,
                unique_filename,
                ExtraArgs={'ContentType': self._get_content_type(filename)}
            )
            
            # Return public URL
            if settings.R2_BUCKET_URL:
                # Ensure URL ends with / if it doesn't already
                base_url = settings.R2_BUCKET_URL.rstrip('/')
                return f"{base_url}/{unique_filename}"
            else:
                # Fallback: construct URL from endpoint and bucket
                endpoint_base = settings.R2_ENDPOINT_URL.replace('https://', '').replace('http://', '').split('/')[0]
                return f"https://{endpoint_base}/{self.bucket_name}/{unique_filename}"
        except Exception as e:
            print(f"R2 Upload Error: {type(e).__name__}: {str(e)}")
            print(f"Bucket: {self.bucket_name}, Endpoint: {settings.R2_ENDPOINT_URL}")
            raise
    
    def delete_file(self, file_path: str) -> bool:
        """Delete file from R2"""
        try:
            # Extract key from URL
            key = file_path.split(settings.R2_BUCKET_URL + '/')[-1] if settings.R2_BUCKET_URL in file_path else file_path
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as e:
            print(f"Error deleting file from R2: {e}")
            return False
    
    def _get_content_type(self, filename: str) -> str:
        """Get content type based on file extension"""
        ext = filename.split('.')[-1].lower()
        content_types = {
            'pdf': 'application/pdf',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'txt': 'text/plain',
            'csv': 'text/csv',
            'mp4': 'video/mp4',
            'mov': 'video/quicktime'
        }
        return content_types.get(ext, 'application/octet-stream')

