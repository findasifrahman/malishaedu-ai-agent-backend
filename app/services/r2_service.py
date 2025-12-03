import boto3
from botocore.config import Config
from app.config import settings
from typing import BinaryIO, Union
import uuid
from datetime import datetime
import io

class R2Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            config=Config(signature_version='s3v4')
        )
        # Extract bucket name from URL or use directly
        if '/' in settings.R2_BUCKET_URL:
            self.bucket_name = settings.R2_BUCKET_URL.split('/')[-1].split('?')[0]
        else:
            self.bucket_name = settings.R2_BUCKET_URL
    
    def upload_file(self, file: Union[BinaryIO, bytes, io.BytesIO], filename: str, folder: str = "documents") -> str:
        """Upload file to R2 and return public URL"""
        # Generate unique filename
        file_ext = filename.split('.')[-1] if '.' in filename else ''
        unique_filename = f"{folder}/{uuid.uuid4()}.{file_ext}" if file_ext else f"{folder}/{uuid.uuid4()}"
        
        # Convert bytes to file-like object if needed
        if isinstance(file, bytes):
            file = io.BytesIO(file)
        
        # Upload to R2
        self.s3_client.upload_fileobj(
            file,
            self.bucket_name,
            unique_filename,
            ExtraArgs={'ContentType': self._get_content_type(filename)}
        )
        
        # Return public URL
        return f"{settings.R2_BUCKET_URL}/{unique_filename}"
    
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

