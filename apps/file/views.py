from django.http import FileResponse
from django.db.models import Q
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.user.permissions import IsAdmin
from apps.user.roles import get_active_role

from .models import FileAuditLog, FileTemplate, ManagedFile
from .serializers import (
    FileTemplateSerializer,
    FileTemplateWriteSerializer,
    ManagedFileSerializer,
    ManagedFileUploadSerializer,
)


def get_accessible_file_queryset(user, request=None):
    queryset = ManagedFile.objects.all()
    if (get_active_role(request) if request else user.profile.role) == 'admin':
        return queryset

    return queryset.filter(
        Q(uploaded_by=user)
        | Q(template_bindings__is_active=True)
        | Q(events_as_attachment__teacher=user)
        | Q(events_as_attachment__status='published')
        | Q(events_as_attachment__status='closed')
        | Q(applications_as_resume__student=user)
        | Q(applications_as_resume__event__teacher=user)
    ).distinct()


def create_file_audit_log(request, action, *, managed_file=None, template_key="", metadata=None):
    FileAuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        managed_file=managed_file,
        template_key=template_key,
        ip_address=request.META.get("REMOTE_ADDR", ""),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        metadata=metadata or {},
    )


class ManagedFileUploadView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManagedFileUploadSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        create_file_audit_log(
            request,
            FileAuditLog.ACTION_UPLOAD,
            managed_file=instance,
            metadata={"category": instance.category},
        )
        data = ManagedFileSerializer(instance, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)


class ManagedFileListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManagedFileSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = ManagedFile.objects.all() if get_active_role(self.request) == 'admin' else ManagedFile.objects.filter(uploaded_by=user)

        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)

        return queryset


class ManagedFileDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ManagedFileSerializer
    lookup_field = "id"

    def get_queryset(self):
        return get_accessible_file_queryset(self.request.user, self.request)

    def perform_destroy(self, instance):
        user = self.request.user
        if get_active_role(self.request) != 'admin' and instance.uploaded_by_id != user.id:
            raise NotFound("This file does not exist.")
        if not instance.can_delete():
            create_file_audit_log(
                self.request,
                FileAuditLog.ACTION_DELETE_BLOCKED,
                managed_file=instance,
                metadata={"reason": "referenced"},
            )
            raise ValidationError("Referenced files cannot be deleted.")

        storage = instance.file.storage
        file_name = instance.file.name
        create_file_audit_log(
            self.request,
            FileAuditLog.ACTION_DELETE,
            managed_file=instance,
            metadata={"original_name": instance.original_name},
        )
        instance.delete()
        if file_name:
            storage.delete(file_name)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ManagedFileDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        file_obj = get_accessible_file_queryset(request.user, request).filter(id=id).first()
        if file_obj is None or not file_obj.file:
            raise NotFound("This file does not exist.")

        create_file_audit_log(
            request,
            FileAuditLog.ACTION_DOWNLOAD,
            managed_file=file_obj,
        )
        response = FileResponse(file_obj.file.open("rb"), as_attachment=True, filename=file_obj.original_name)
        if file_obj.content_type:
            response["Content-Type"] = file_obj.content_type
        return response


class FileTemplateListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FileTemplateSerializer

    def get_queryset(self):
        return FileTemplate.objects.filter(is_active=True).select_related("file")


class FileTemplateDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FileTemplateSerializer
    lookup_field = "key"

    def get_queryset(self):
        return FileTemplate.objects.filter(is_active=True).select_related("file")


class AdminFileTemplateListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        queryset = FileTemplate.objects.select_related("file")
        is_active = self.request.query_params.get("is_active")
        if is_active == "true":
            queryset = queryset.filter(is_active=True)
        elif is_active == "false":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_serializer_class(self):
        if self.request.method == "POST":
            return FileTemplateWriteSerializer
        return FileTemplateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        create_file_audit_log(
            request,
            FileAuditLog.ACTION_TEMPLATE_CREATE,
            managed_file=template.file,
            template_key=template.key,
        )
        data = FileTemplateSerializer(template, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)


class AdminFileTemplateDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = "key"

    def get_queryset(self):
        return FileTemplate.objects.select_related("file")

    def get_serializer_class(self):
        if self.request.method in {"PUT", "PATCH"}:
            return FileTemplateWriteSerializer
        return FileTemplateSerializer

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        create_file_audit_log(
            request,
            FileAuditLog.ACTION_TEMPLATE_UPDATE,
            managed_file=template.file,
            template_key=template.key,
        )
        data = FileTemplateSerializer(template, context={"request": request}).data
        return Response(data)

    def perform_destroy(self, instance):
        # Templates are disabled instead of physically deleted to keep references stable.
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        create_file_audit_log(
            self.request,
            FileAuditLog.ACTION_TEMPLATE_DISABLE,
            managed_file=instance.file,
            template_key=instance.key,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        data = FileTemplateSerializer(instance, context={"request": request}).data
        return Response(data, status=status.HTTP_200_OK)
