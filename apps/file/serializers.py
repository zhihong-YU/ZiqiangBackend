import os

from rest_framework import serializers
from django.urls import reverse

from .models import FileTemplate, ManagedFile


FILE_UPLOAD_RULES = {
    ManagedFile.CATEGORY_EVENT_ATTACHMENT: {
        "extensions": {".zip", ".rar", ".7z", ".pdf", ".doc", ".docx"},
        "max_size": 100 * 1024 * 1024,
    },
    ManagedFile.CATEGORY_APPLICATION_RESUME: {
        "extensions": {".pdf", ".doc", ".docx", ".zip"},
        "max_size": 20 * 1024 * 1024,
    },
    ManagedFile.CATEGORY_TEMPLATE: {
        "extensions": {".pdf", ".doc", ".docx", ".zip"},
        "max_size": 20 * 1024 * 1024,
    },
    ManagedFile.CATEGORY_OTHER: {
        "extensions": {".zip", ".rar", ".7z", ".pdf", ".doc", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"},
        "max_size": 50 * 1024 * 1024,
    },
}


class ManagedFileSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    is_referenced = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = ManagedFile
        fields = [
            "id",
            "original_name",
            "category",
            "content_type",
            "file_size",
            "description",
            "file_url",
            "is_referenced",
            "can_delete",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj):
        if not obj.file:
            return ""
        url = reverse("file-download", kwargs={"id": obj.id})
        return url

    def get_is_referenced(self, obj):
        return obj.is_referenced()

    def get_can_delete(self, obj):
        return obj.can_delete()


class ManagedFileUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)

    class Meta:
        model = ManagedFile
        fields = ["id", "file", "category", "description"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        uploaded_file = attrs["file"]
        category = attrs["category"]
        rule = FILE_UPLOAD_RULES[category]
        extension = os.path.splitext(uploaded_file.name)[1].lower()

        if extension not in rule["extensions"]:
            raise serializers.ValidationError(
                f"Unsupported file type for category '{category}': {extension or 'no extension'}."
            )

        if getattr(uploaded_file, "size", 0) > rule["max_size"]:
            raise serializers.ValidationError(
                f"File is too large for category '{category}'. Max size is {rule['max_size']} bytes."
            )

        return attrs

    def create(self, validated_data):
        uploaded_file = validated_data.pop("file")
        user = self.context["request"].user
        return ManagedFile.objects.create(
            file=uploaded_file,
            original_name=uploaded_file.name,
            file_size=getattr(uploaded_file, "size", 0),
            content_type=getattr(uploaded_file, "content_type", "") or "",
            uploaded_by=user,
            **validated_data,
        )


class FileTemplateSerializer(serializers.ModelSerializer):
    file = ManagedFileSerializer(read_only=True)

    class Meta:
        model = FileTemplate
        fields = [
            "key",
            "name",
            "description",
            "file",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FileTemplateWriteSerializer(serializers.ModelSerializer):
    file_id = serializers.PrimaryKeyRelatedField(
        source="file",
        queryset=ManagedFile.objects.all(),
    )

    class Meta:
        model = FileTemplate
        fields = [
            "key",
            "name",
            "description",
            "file_id",
            "is_active",
        ]

    def validate_file_id(self, value):
        if value.category != ManagedFile.CATEGORY_TEMPLATE:
            raise serializers.ValidationError("Template file must use category 'template'.")
        return value
