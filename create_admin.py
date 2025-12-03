"""
Script to create default admin user
Run: python create_admin.py
"""
from app.database import SessionLocal
from app.models import User, UserRole
import bcrypt

def get_password_hash(password: str) -> str:
    """Hash password using bcrypt (same as auth router)"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def create_admin_user():
    db = SessionLocal()
    try:
        # Check if admin already exists
        existing_admin = db.query(User).filter(
            User.email == "findasifrahman@gmail.com"
        ).first()
        
        if existing_admin:
            print("Admin user already exists!")
            # Update role to admin if not already
            if existing_admin.role != UserRole.ADMIN:
                existing_admin.role = UserRole.ADMIN
                db.commit()
                print("Updated existing user to admin role.")
            return
        
        # Create admin user
        password = "Asif@10018"
        admin_user = User(
            email="findasifrahman@gmail.com",
            name="asif",
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN
        )
        
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        
        print(f"✓ Admin user created successfully!")
        print(f"  Email: {admin_user.email}")
        print(f"  Name: {admin_user.name}")
        print(f"  Role: {admin_user.role.value}")
        
    except Exception as e:
        print(f"Error creating admin user: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_admin_user()

