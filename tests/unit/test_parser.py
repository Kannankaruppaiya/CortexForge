"""Unit tests for multi-language Tree-sitter AST parsing."""

import pytest

from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider


@pytest.fixture
def provider() -> TreeSitterProvider:
    return TreeSitterProvider()


def test_python_parsing(provider: TreeSitterProvider) -> None:
    code = b"""
import os
from typing import Optional, List

class BaseService:
    def base_method(self):
        pass

class UserService(BaseService):
    def __init__(self, db_url: str):
        self.db = db_url

    async def get_user(self, user_id: int) -> Optional[dict]:
        return {"id": user_id}

def calculate_hash(data: str) -> str:
    return "mock-hash"
"""
    result = provider.parse_source("services/user_service.py", code)
    assert result.error is None
    assert result.language == "python"

    symbol_names = {s.name for s in result.symbols}
    assert "BaseService" in symbol_names
    assert "UserService" in symbol_names
    assert "get_user" in symbol_names
    assert "calculate_hash" in symbol_names

    # Check classes & inheritance
    user_service_sym = next(s for s in result.symbols if s.name == "UserService")
    assert user_service_sym.entity_type == "class"
    assert "BaseService" in user_service_sym.metadata.get("bases", [])

    # Check relationships
    rel_types = {
        (r.relationship_type, r.target_qualified_name) for r in result.relationships
    }
    assert ("inherits", "BaseService") in rel_types
    assert ("imports", "os") in rel_types


def test_typescript_parsing(provider: TreeSitterProvider) -> None:
    code = b"""
import { useState, useEffect } from 'react';

export interface UserProfile {
    id: string;
    username: string;
}

export class AuthManager {
    login(token: string): boolean {
        return true;
    }
}

export function validateToken(token: string): boolean {
    return token.length > 0;
}
"""
    result = provider.parse_source("auth/auth.ts", code)
    assert result.error is None
    assert result.language == "typescript"

    symbol_names = {s.name for s in result.symbols}
    assert "UserProfile" in symbol_names
    assert "AuthManager" in symbol_names
    assert "login" in symbol_names
    assert "validateToken" in symbol_names

    # Check interface vs class
    iface = next(s for s in result.symbols if s.name == "UserProfile")
    assert iface.entity_type == "interface"

    cls = next(s for s in result.symbols if s.name == "AuthManager")
    assert cls.entity_type == "class"

    rel_types = {
        (r.relationship_type, r.target_qualified_name) for r in result.relationships
    }
    assert ("imports", "react") in rel_types


def test_go_parsing(provider: TreeSitterProvider) -> None:
    code = b"""
package auth

import "fmt"

type TokenPayload struct {
    UserID string
    Role   string
}

type TokenValidator interface {
    Validate(token string) bool
}

func GenerateToken(userID string) string {
    return fmt.Sprintf("token-%s", userID)
}

func (p *TokenPayload) IsAdmin() bool {
    return p.Role == "admin"
}
"""
    result = provider.parse_source("pkg/auth/token.go", code)
    assert result.error is None
    assert result.language == "go"

    symbol_names = {s.name for s in result.symbols}
    assert "TokenPayload" in symbol_names
    assert "TokenValidator" in symbol_names
    assert "GenerateToken" in symbol_names
    assert "IsAdmin" in symbol_names

    rel_types = {
        (r.relationship_type, r.target_qualified_name) for r in result.relationships
    }
    assert ("imports", "fmt") in rel_types


def test_java_parsing(provider: TreeSitterProvider) -> None:
    code = b"""
package com.example.service;

import java.util.List;

public interface Repository<T> {
    T findById(Long id);
}

public class OrderService {
    public void processOrder(Long id) {
        // process
    }
}
"""
    result = provider.parse_source("src/main/java/OrderService.java", code)
    assert result.error is None
    assert result.language == "java"

    symbol_names = {s.name for s in result.symbols}
    assert "Repository" in symbol_names
    assert "OrderService" in symbol_names
    assert "processOrder" in symbol_names
